from edwh_devdb_plugin.devdb_plugin import _list_line_table_name


def test_list_line_table_name_table_data():
    line = "4441; 0 16561 TABLE DATA click event postgres\n"
    assert _list_line_table_name(line) == ("click", "event")


def test_list_line_table_name_mat_view():
    line = "4515; 0 205702816 MATERIALIZED VIEW DATA public bestuur_stats postgres\n"
    assert _list_line_table_name(line) == ("public", "bestuur_stats")


def test_list_line_table_name_no_data():
    line = "444; 1259 16629 TABLE public board_ed postgres\n"
    assert _list_line_table_name(line) == (None, None)


def test_recover_signature_has_new_flags():
    import inspect

    from edwh_devdb_plugin.devdb_plugin import recover

    params = inspect.signature(recover).parameters
    for name in ("include", "exclude", "data_only", "with_schema", "clean", "disable_triggers"):
        assert name in params, f"missing param {name}"
