import pytest
from piton.native_differential import compare_native_to_cpython

@pytest.mark.parametrize("source, expected_output, expected_exit_code", [
    # Test cases for _strptime
    ("from _strptime import LocaleTime; print(LocaleTime().strptime('2023-01-01', '%Y-%m-%d'))", "2023-01-01", 0),
    ("from _strptime import LocaleTime; print(LocaleTime().strptime('invalid-date', '%Y-%m-%d'))", "ValueError", 1),
    
    # Test cases for threading
    ("import threading; print(threading.current_thread().name)", "MainThread", 0),
    ("import threading; print(threading.Thread().name)", "Thread-", 0),
    
    # Test cases for cgi
    ("import cgi; print(cgi.log('test'))", "test", 0),
    ("import cgi; print(cgi.log(123))", "123", 0),
])
def test_linker_errors(source, expected_output, expected_exit_code):
    compare_native_to_cpython(source, expected_output, expected_exit_code)
