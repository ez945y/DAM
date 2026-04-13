import pytest

from dam.registry.callback import CallbackRegistry


def test_register_and_get():
    reg = CallbackRegistry()

    def fn(obs):
        return True

    reg.register("my_check", fn)
    assert reg.get("my_check") is fn


def test_duplicate_registration_raises():
    reg = CallbackRegistry()
    reg.register("check", lambda: True)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("check", lambda: True)


def test_get_missing_raises():
    reg = CallbackRegistry()
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_list_all_sorted():
    reg = CallbackRegistry()
    reg.register("b_check", lambda: True)
    reg.register("a_check", lambda: True)
    assert reg.list_all() == ["a_check", "b_check"]


def test_valid_keys_filtering():
    reg = CallbackRegistry()
    with pytest.raises(ValueError, match="unknown parameter"):
        reg.register("check", lambda unknown_key: True, valid_keys={"obs", "action"})
