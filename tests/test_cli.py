import json

from syncwatch.cli import main


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def setup(tmp_path, dest_rows):
    m = write(tmp_path / "m.json", {"key": "email", "fields": {"email": "email"}})
    s = write(tmp_path / "s.json", {"records": [{"email": "a@x.com"}]})
    d = write(tmp_path / "d.json", {"records": dest_rows})
    return s, d, m


def test_exit_0_when_clean(tmp_path):
    s, d, m = setup(tmp_path, [{"email": "a@x.com"}])
    assert main(["check", "--source", s, "--dest", d, "--mapping", m]) == 0


def test_exit_1_when_broken(tmp_path):
    s, d, m = setup(tmp_path, [])
    assert main(["check", "--source", s, "--dest", d, "--mapping", m]) == 1


def test_exit_2_when_it_cannot_see(tmp_path, capsys):
    s, _, m = setup(tmp_path, [])
    code = main(["check", "--source", s, "--dest", str(tmp_path / "gone.json"), "--mapping", m])
    assert code == 2
    assert "cannot check" in capsys.readouterr().err


def test_exit_2_on_malformed_json(tmp_path):
    s, _, m = setup(tmp_path, [])
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["check", "--source", s, "--dest", str(bad), "--mapping", m]) == 2


def test_mapping_key_must_be_mapped(tmp_path):
    s, d, _ = setup(tmp_path, [])
    m = write(tmp_path / "m2.json", {"key": "id", "fields": {"email": "email"}})
    assert main(["check", "--source", s, "--dest", d, "--mapping", m]) == 2


def test_json_output_is_parseable(tmp_path, capsys):
    s, d, m = setup(tmp_path, [])
    main(["check", "--source", s, "--dest", d, "--mapping", m, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["clean"] is False and payload["broken"] >= 1


def test_findings_still_print_when_state_cannot_be_saved(tmp_path, capsys):
    s, d, m = setup(tmp_path, [])
    code = main(["check", "--source", s, "--dest", d, "--mapping", m,
                 "--state", str(tmp_path / "no" / "such" / "dir" / "state.json")])
    out, err = capsys.readouterr()
    assert code == 2
    assert "unsynced_records" in out
    assert "could not save state" in err
