def test_package_is_importable():
    import agent_lens

    assert agent_lens.__version__ == "0.1.0"
