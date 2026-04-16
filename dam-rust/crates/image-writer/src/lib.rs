//! ImageWriter — async JPEG encoding for MCAP image capture (fire-and-forget).
//!
//! Encodes images to JPEG bytes on a thread pool without returning results.
//! Designed for fire-and-forget submission from Python hot path.
//!
//! # Usage
//! ```ignore
//! use image_writer::ImageWriter;
//!
//! let writer = ImageWriter::new();
//! // Submit encoding task asynchronously; returns immediately
//! ImageWriter::submit_async(
//!     vec![0u8; 640 * 480 * 3],  // RGB bytes
//!     640,                        // width
//!     480,                        // height
//!     85,                         // quality
//!     |bytes| {
//!         // Callback receives encoded JPEG bytes
//!         eprintln!("Encoded {} bytes", bytes.len());
//!     }
//! );
//! ```

use image::{ImageBuffer, Rgb};

/// Async JPEG encoder without return tracking.
/// Fire-and-forget design for hot-path Python integration.
pub struct ImageWriter {
    // Marker for thread pool coordination (global rayon pool used).
    _marker: std::marker::PhantomData<()>,
}

impl ImageWriter {
    /// Create a new ImageWriter (singleton pattern — uses global rayon pool).
    pub fn new() -> Self {
        Self {
            _marker: std::marker::PhantomData,
        }
    }

    /// Encode a single image to JPEG synchronously.
    /// Returns encoded JPEG bytes on success.
    pub fn encode_jpeg(
        data: &[u8],
        width: u32,
        height: u32,
        quality: u8,
    ) -> Result<Vec<u8>, String> {
        // Validate dimensions
        let expected_len = (width as usize) * (height as usize) * 3;
        if data.len() != expected_len {
            return Err(format!(
                "Invalid image data: expected {} bytes for {}x{} RGB, got {}",
                expected_len,
                width,
                height,
                data.len()
            ));
        }

        // Quality must be 1-100
        let quality = quality.clamp(1, 100);

        // Create image buffer from RGB bytes
        let img: ImageBuffer<Rgb<u8>, Vec<u8>> =
            ImageBuffer::from_raw(width, height, data.to_vec())
                .ok_or_else(|| "Failed to create image buffer".to_string())?;

        // Encode to JPEG
        let mut jpeg_bytes = Vec::with_capacity((width as usize) * (height as usize));

        let encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut jpeg_bytes, quality);
        img.write_with_encoder(encoder)
            .map_err(|e| format!("JPEG encoding failed: {}", e))?;

        Ok(jpeg_bytes)
    }

    /// Submit a single image for async JPEG encoding (fire-and-forget).
    /// Spawns encoding on a thread pool and immediately returns.
    /// The callback will be invoked with encoded bytes when ready.
    ///
    /// # Safety
    /// This is designed for Python interop. The callback must be safe to call
    /// from a background thread.
    pub fn submit_async<F>(data: Vec<u8>, width: u32, height: u32, quality: u8, callback: F)
    where
        F: FnOnce(Result<Vec<u8>, String>) + Send + 'static,
    {
        std::thread::spawn(move || {
            let result = Self::encode_jpeg(&data, width, height, quality);
            callback(result);
        });
    }
}

impl Default for ImageWriter {
    fn default() -> Self {
        Self::new()
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    fn create_test_image(width: u32, height: u32) -> Vec<u8> {
        vec![128u8; (width * height * 3) as usize]
    }

    #[test]
    fn encode_single_image() {
        let data = create_test_image(100, 100);
        let result = ImageWriter::encode_jpeg(&data, 100, 100, 85);
        assert!(result.is_ok());
        let jpeg = result.unwrap();
        // JPEG should have some size
        assert!(jpeg.len() > 100);
        // JPEG magic bytes: FF D8
        assert_eq!(jpeg[0], 0xFF);
        assert_eq!(jpeg[1], 0xD8);
    }

    #[test]
    fn submit_async_fire_and_forget() {
        let data = create_test_image(200, 200);
        let _writer = ImageWriter::new();

        let counter = Arc::new(AtomicUsize::new(0));
        let counter_clone = Arc::clone(&counter);

        // Submit async without waiting
        ImageWriter::submit_async(data, 200, 200, 85, move |result| {
            assert!(result.is_ok());
            counter_clone.fetch_add(1, Ordering::SeqCst);
        });

        // Give thread time to encode
        std::thread::sleep(std::time::Duration::from_millis(100));

        // Callback should have been called
        assert_eq!(counter.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn invalid_dimensions() {
        let data = vec![0u8; 100]; // Wrong size
        let result = ImageWriter::encode_jpeg(&data, 10, 10, 85);
        assert!(result.is_err());
    }
}
