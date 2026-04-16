//! SerializerBus — high-performance JSON serialization for DAM cycle records.
//!
//! Converts a CycleRecord dict (from Python) into 6 pre-serialized JSON byte strings.
//! No state, no GIL contention — pure Rust JSON serialization via `serde_json`.
//!
//! # Design
//!
//! Input: Python dict representation of CycleRecord (all arrays already list[float])
//! Output: Dict mapping topic -> bytes (or list[bytes] for /dam/L0-L4)
//!
//! This allows Python MCAP writer to stay untouched while removing JSON
//! serialization from the hot path.

use std::collections::HashMap;

/// Stateless JSON serializer for DAM cycle records.
pub struct SerializerBus;

impl SerializerBus {
    /// Serialize a CycleRecord dict to pre-serialized JSON messages.
    ///
    /// Input dict structure (mirrors Python CycleRecord):
    /// ```python
    /// {
    ///     "cycle_id": int,
    ///     "obs_timestamp": float,
    ///     "active_task": str | None,
    ///     "active_boundaries": [str, ...],
    ///     "has_violation": bool,
    ///     "has_clamp": bool,
    ///     "violated_layer_mask": int,
    ///     "clamped_layer_mask": int,
    ///     "obs_joint_positions": [float, ...],
    ///     "obs_joint_velocities": [float, ...] | None,
    ///     "obs_end_effector_pose": [float, ...] | None,
    ///     "obs_force_torque": [float, ...] | None,
    ///     "action_positions": [float, ...],
    ///     "action_velocities": [float, ...] | None,
    ///     "validated_positions": [float, ...] | None,
    ///     "validated_velocities": [float, ...] | None,
    ///     "was_clamped": bool,
    ///     "fallback_triggered": str | None,
    ///     "guard_results": [
    ///         {
    ///             "guard_name": str,
    ///             "layer": int,
    ///             "decision": int,
    ///             "decision_name": str,
    ///             "reason": str,
    ///             "latency_ms": float | None,
    ///             "is_violation": bool,
    ///             "is_clamp": bool,
    ///             "fault_source": str | None,
    ///         },
    ///         ...
    ///     ],
    ///     "latency_stages": {"source": float, "policy": float, ...},
    ///     "latency_layers": {"L0": float, "L1": float, ...},
    ///     "latency_guards": {"guard_name": float, ...},
    /// }
    /// ```
    ///
    /// Output structure:
    /// ```python
    /// {
    ///     "/dam/cycle": b'{"cycle_id": ...}',
    ///     "/dam/obs": b'{"cycle_id": ...}',
    ///     "/dam/action": b'{"cycle_id": ...}',
    ///     "/dam/L0": [b'...', b'...', ...],  # list if guard_results present
    ///     "/dam/L1": [...],
    ///     "/dam/L2": [...],
    ///     "/dam/L3": [...],
    ///     "/dam/L4": [...],
    ///     "/dam/latency": b'{"cycle_id": ...}',
    /// }
    /// ```
    pub fn serialize_cycle(
        record: &serde_json::Value,
    ) -> Result<HashMap<String, serde_json::Value>, String> {
        let mut result = HashMap::new();

        // Extract common fields
        let _cycle_id = record
            .get("cycle_id")
            .and_then(|v| v.as_u64())
            .ok_or("Missing or invalid cycle_id")?;
        let _obs_timestamp = record
            .get("obs_timestamp")
            .and_then(|v| v.as_f64())
            .ok_or("Missing or invalid obs_timestamp")?;

        // 1. /dam/cycle
        let cycle_msg = Self::build_cycle_message(record)?;
        let cycle_bytes = serde_json::to_vec(&cycle_msg)
            .map_err(|e| format!("Failed to serialize cycle message: {}", e))?;
        result.insert(
            "/dam/cycle".to_string(),
            serde_json::Value::String(
                String::from_utf8(cycle_bytes)
                    .map_err(|e| format!("Invalid UTF-8 in cycle message: {}", e))?,
            ),
        );

        // 2. /dam/obs
        let obs_msg = Self::build_observation_message(record)?;
        let obs_bytes = serde_json::to_vec(&obs_msg)
            .map_err(|e| format!("Failed to serialize observation message: {}", e))?;
        result.insert(
            "/dam/obs".to_string(),
            serde_json::Value::String(
                String::from_utf8(obs_bytes)
                    .map_err(|e| format!("Invalid UTF-8 in obs message: {}", e))?,
            ),
        );

        // 3. /dam/action
        let action_msg = Self::build_action_message(record)?;
        let action_bytes = serde_json::to_vec(&action_msg)
            .map_err(|e| format!("Failed to serialize action message: {}", e))?;
        result.insert(
            "/dam/action".to_string(),
            serde_json::Value::String(
                String::from_utf8(action_bytes)
                    .map_err(|e| format!("Invalid UTF-8 in action message: {}", e))?,
            ),
        );

        // 4. /dam/L0 … /dam/L4 (one message per guard result, grouped by layer)
        let guard_results = record
            .get("guard_results")
            .and_then(|v| v.as_array())
            .ok_or("Missing or invalid guard_results")?;

        // Group guards by layer
        let mut by_layer: HashMap<i64, Vec<serde_json::Value>> = HashMap::new();
        for guard_result in guard_results {
            let layer = guard_result
                .get("layer")
                .and_then(|v| v.as_i64())
                .ok_or("Missing or invalid guard layer")?;

            let guard_msg = Self::build_guard_result_message(record, guard_result)?;
            by_layer.entry(layer).or_default().push(guard_msg);
        }

        // Write L0-L4 (each layer as an array of JSON strings)
        for layer in 0..5 {
            let topic = format!("/dam/L{}", layer);
            if let Some(messages) = by_layer.get(&layer) {
                let mut json_messages = Vec::new();
                for msg in messages {
                    let bytes = serde_json::to_vec(msg)
                        .map_err(|e| format!("Failed to serialize guard message: {}", e))?;
                    let json_str =
                        String::from_utf8(bytes).map_err(|e| format!("Invalid UTF-8: {}", e))?;
                    json_messages.push(serde_json::Value::String(json_str));
                }

                result.insert(topic, serde_json::Value::Array(json_messages));
            } else {
                // No guards for this layer — empty array
                result.insert(topic, serde_json::Value::Array(vec![]));
            }
        }

        // 5. /dam/latency
        let latency_msg = Self::build_latency_message(record)?;
        let latency_bytes = serde_json::to_vec(&latency_msg)
            .map_err(|e| format!("Failed to serialize latency message: {}", e))?;
        result.insert(
            "/dam/latency".to_string(),
            serde_json::Value::String(
                String::from_utf8(latency_bytes)
                    .map_err(|e| format!("Invalid UTF-8 in latency message: {}", e))?,
            ),
        );

        Ok(result)
    }

    // ── Message builders ──────────────────────────────────────────────────

    fn build_cycle_message(record: &serde_json::Value) -> Result<serde_json::Value, String> {
        let latency_stages = record
            .get("latency_stages")
            .and_then(|v| v.as_object())
            .ok_or("Missing latency_stages")?;

        Ok(serde_json::json!({
            "cycle_id": record.get("cycle_id").ok_or("Missing cycle_id")?,
            "timestamp": record.get("obs_timestamp").ok_or("Missing obs_timestamp")?,
            "active_task": record.get("active_task"),
            "active_boundaries": record.get("active_boundaries").ok_or("Missing active_boundaries")?,
            "has_violation": record.get("has_violation").ok_or("Missing has_violation")?,
            "has_clamp": record.get("has_clamp").ok_or("Missing has_clamp")?,
            "violated_layer_mask": record.get("violated_layer_mask").ok_or("Missing violated_layer_mask")?,
            "clamped_layer_mask": record.get("clamped_layer_mask").ok_or("Missing clamped_layer_mask")?,
            "source_ms": latency_stages.get("source").cloned().unwrap_or(serde_json::json!(0.0)),
            "policy_ms": latency_stages.get("policy").cloned().unwrap_or(serde_json::json!(0.0)),
            "guards_ms": latency_stages.get("guards").cloned().unwrap_or(serde_json::json!(0.0)),
            "sink_ms": latency_stages.get("sink").cloned().unwrap_or(serde_json::json!(0.0)),
            "total_ms": latency_stages.get("total").cloned().unwrap_or(serde_json::json!(0.0)),
        }))
    }

    fn build_observation_message(record: &serde_json::Value) -> Result<serde_json::Value, String> {
        let mut msg = serde_json::json!({
            "cycle_id": record.get("cycle_id").ok_or("Missing cycle_id")?,
            "timestamp": record.get("obs_timestamp").ok_or("Missing obs_timestamp")?,
            "joint_positions": record.get("obs_joint_positions").ok_or("Missing obs_joint_positions")?,
        });

        if let Some(vels) = record.get("obs_joint_velocities") {
            if !vels.is_null() {
                msg["joint_velocities"] = vels.clone();
            }
        }
        if let Some(pose) = record.get("obs_end_effector_pose") {
            if !pose.is_null() {
                msg["end_effector_pose"] = pose.clone();
            }
        }
        if let Some(ft) = record.get("obs_force_torque") {
            if !ft.is_null() {
                msg["force_torque"] = ft.clone();
            }
        }

        Ok(msg)
    }

    fn build_action_message(record: &serde_json::Value) -> Result<serde_json::Value, String> {
        let mut msg = serde_json::json!({
            "cycle_id": record.get("cycle_id").ok_or("Missing cycle_id")?,
            "timestamp": record.get("obs_timestamp").ok_or("Missing obs_timestamp")?,
            "target_positions": record.get("action_positions").ok_or("Missing action_positions")?,
            "was_clamped": record.get("was_clamped").ok_or("Missing was_clamped")?,
            "fallback_triggered": record.get("fallback_triggered"),
        });

        if let Some(vels) = record.get("action_velocities") {
            if !vels.is_null() {
                msg["target_velocities"] = vels.clone();
            }
        }
        if let Some(positions) = record.get("validated_positions") {
            if !positions.is_null() {
                msg["validated_positions"] = positions.clone();
            }
        }
        if let Some(vels) = record.get("validated_velocities") {
            if !vels.is_null() {
                msg["validated_velocities"] = vels.clone();
            }
        }

        Ok(msg)
    }

    fn build_guard_result_message(
        record: &serde_json::Value,
        result: &serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let guard_name = result
            .get("guard_name")
            .and_then(|v| v.as_str())
            .ok_or("Missing guard_name")?;

        let latency_guards = record
            .get("latency_guards")
            .and_then(|v| v.as_object())
            .ok_or("Missing latency_guards")?;

        Ok(serde_json::json!({
            "cycle_id": record.get("cycle_id").ok_or("Missing cycle_id")?,
            "timestamp": record.get("obs_timestamp").ok_or("Missing obs_timestamp")?,
            "guard_name": guard_name,
            "layer": result.get("layer").ok_or("Missing guard layer")?,
            "decision": result.get("decision").ok_or("Missing decision")?,
            "decision_name": result.get("decision_name").ok_or("Missing decision_name")?,
            "reason": result.get("reason").ok_or("Missing reason")?,
            "latency_ms": latency_guards.get(guard_name),
            "is_violation": result.get("is_violation").ok_or("Missing is_violation")?,
            "is_clamp": result.get("is_clamp").ok_or("Missing is_clamp")?,
            "fault_source": result.get("fault_source"),
        }))
    }

    fn build_latency_message(record: &serde_json::Value) -> Result<serde_json::Value, String> {
        let latency_stages = record
            .get("latency_stages")
            .and_then(|v| v.as_object())
            .ok_or("Missing latency_stages")?;

        let latency_layers = record
            .get("latency_layers")
            .and_then(|v| v.as_object())
            .ok_or("Missing latency_layers")?;

        let mut msg = serde_json::json!({
            "cycle_id": record.get("cycle_id").ok_or("Missing cycle_id")?,
            "timestamp": record.get("obs_timestamp").ok_or("Missing obs_timestamp")?,
            "source_ms": latency_stages.get("source").cloned().unwrap_or(serde_json::json!(0.0)),
            "policy_ms": latency_stages.get("policy").cloned().unwrap_or(serde_json::json!(0.0)),
            "guards_ms": latency_stages.get("guards").cloned().unwrap_or(serde_json::json!(0.0)),
            "sink_ms": latency_stages.get("sink").cloned().unwrap_or(serde_json::json!(0.0)),
            "total_ms": latency_stages.get("total").cloned().unwrap_or(serde_json::json!(0.0)),
        });

        // Add per-layer latencies
        for layer in 0..5 {
            let key = format!("L{}", layer);
            if let Some(ms) = latency_layers.get(&key) {
                msg[format!("{}_ms", key)] = ms.clone();
            }
        }

        Ok(msg)
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_record() -> serde_json::Value {
        serde_json::json!({
            "cycle_id": 1u64,
            "obs_timestamp": 1234.567,
            "active_task": null,
            "active_boundaries": ["boundary_0", "boundary_1"],
            "has_violation": false,
            "has_clamp": false,
            "violated_layer_mask": 0,
            "clamped_layer_mask": 0,
            "obs_joint_positions": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "obs_joint_velocities": [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3],
            "obs_end_effector_pose": [1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0],
            "obs_force_torque": [0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
            "action_positions": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "action_velocities": null,
            "validated_positions": null,
            "validated_velocities": null,
            "was_clamped": false,
            "fallback_triggered": null,
            "guard_results": vec![
                serde_json::json!({
                    "guard_name": "guard_0",
                    "layer": 0i64,
                    "decision": 0,
                    "decision_name": "PASS",
                    "reason": "pass",
                    "is_violation": false,
                    "is_clamp": false,
                    "fault_source": null,
                }),
            ],
            "latency_stages": {
                "source": 1.0,
                "policy": 2.0,
                "guards": 3.0,
                "sink": 0.5,
                "total": 6.5,
            },
            "latency_layers": {
                "L0": 0.5,
                "L1": 0.6,
                "L2": 0.7,
                "L3": 0.8,
                "L4": 0.9,
            },
            "latency_guards": {
                "guard_0": 0.6,
            },
        })
    }

    #[test]
    fn serialize_cycle_basic() {
        let record = create_test_record();
        let messages = SerializerBus::serialize_cycle(&record).unwrap();

        // Check that all 9 topics are present
        assert!(messages.contains_key("/dam/cycle"));
        assert!(messages.contains_key("/dam/obs"));
        assert!(messages.contains_key("/dam/action"));
        assert!(messages.contains_key("/dam/L0"));
        assert!(messages.contains_key("/dam/L1"));
        assert!(messages.contains_key("/dam/L2"));
        assert!(messages.contains_key("/dam/L3"));
        assert!(messages.contains_key("/dam/L4"));
        assert!(messages.contains_key("/dam/latency"));

        // Check that /dam/cycle is a valid JSON string
        if let Some(serde_json::Value::String(cycle_str)) = messages.get("/dam/cycle") {
            let cycle_json: serde_json::Value =
                serde_json::from_str(cycle_str).expect("Valid JSON");
            assert_eq!(cycle_json["cycle_id"], 1);
        } else {
            panic!("Expected /dam/cycle to be a string");
        }

        // Check that /dam/L0 is an array with one message
        if let Some(serde_json::Value::Array(l0_msgs)) = messages.get("/dam/L0") {
            assert_eq!(l0_msgs.len(), 1, "Expected one guard result for L0");
            if let serde_json::Value::String(msg_str) = &l0_msgs[0] {
                let msg_json: serde_json::Value =
                    serde_json::from_str(msg_str).expect("Valid JSON");
                assert_eq!(msg_json["guard_name"], "guard_0");
            } else {
                panic!("Expected L0 message to be a string");
            }
        } else {
            panic!("Expected /dam/L0 to be an array");
        }
    }

    #[test]
    fn serialize_cycle_no_guards() {
        let mut record = create_test_record();
        record["guard_results"] = serde_json::json!([]);

        let messages = SerializerBus::serialize_cycle(&record).unwrap();

        // L0-L4 should be empty arrays
        for layer in 0..5 {
            let topic = format!("/dam/L{}", layer);
            if let Some(serde_json::Value::Array(msgs)) = messages.get(&topic) {
                assert_eq!(msgs.len(), 0, "Expected empty array for {}", topic);
            } else {
                panic!("Expected {} to be an array", topic);
            }
        }
    }
}
