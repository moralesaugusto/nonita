import pytest

from nonita.connection import parse_connection, parse_port


def test_bare_host_gets_port():
    assert parse_connection(" 10.0.0.5 ", 11434) == ("http://10.0.0.5:11434", None)


def test_full_url_not_given_second_port():
    assert parse_connection("https://ollama.example.com:8443/", 11434) == ("https://ollama.example.com:8443", None)
    assert parse_connection("http://box/", 99999)[0] == "http://box"


@pytest.mark.parametrize("port", [0, 65536, -1, 70000.0])
def test_port_out_of_range(port):
    base, err = parse_connection("host", port)
    assert base is None and "1 to 65535" in err


@pytest.mark.parametrize("port", [None, "", "abc", 1.5, True])
def test_port_not_a_number(port):
    assert parse_port(port)[1] is not None


def test_port_accepts_float_and_string():
    assert parse_port(11434.0) == (11434, None)
    assert parse_port("8080") == (8080, None)


@pytest.mark.parametrize("host", [None, "", "   ", "bad host", "ftp://x", "http://"])
def test_bad_hosts(host):
    base, err = parse_connection(host, 11434)
    assert base is None and err
