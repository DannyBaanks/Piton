#!/usr/bin/env python3
"""
Tests for datetime/time/locale parity with CPython 3.12.3
"""

import datetime
import time
import locale
import tempfile
import os

def test_date_today():
    """Test date.today() reading real clock"""
    # Both sides should run on same day, so dates should match
    d = datetime.date.today()
    assert isinstance(d, datetime.date)
    
def test_date_constructor():
    """Test date(1964,7,31) with exact repr/str, comparisons, attributes"""
    d = datetime.date(1964, 7, 31)
    assert str(d) == '1964-07-31'
    assert repr(d) == 'datetime.date(1964, 7, 31)'
    assert d.year == 1964
    assert d.month == 7
    assert d.day == 31
    assert d.weekday() == 4  # Friday (0=Monday)
    assert d.isoweekday() == 5  # Friday (1=Monday)

def test_date_strftime():
    """Test strftime with various directives"""
    d = datetime.date(1964, 7, 31)
    
    # Test various format directives
    assert d.strftime('%m-%d-%y') == '07-31-64'
    assert d.strftime('%d %b %Y') == '31 Jul 1964'
    assert d.strftime('%A') == 'Friday'
    assert d.strftime('%B') == 'July'
    assert d.strftime('%d') == '31'  # Day without leading zero
    assert d.strftime('%Y') == '1964'
    assert d.strftime('%j') == '213'  # Day of year
    assert d.strftime('%U') == '30'  # Week number (Sunday first), matches CPython
    assert d.strftime('%W') == '30'  # Week number (Monday first)

def test_timedelta():
    """Test timedelta properties and arithmetic"""
    td = datetime.timedelta(days=10, seconds=3600)
    assert td.days == 10
    assert td.seconds == 3600
    assert td.total_seconds() == 864000 + 3600  # 10*86400 + 3600
    
    # Test arithmetic
    d1 = datetime.date(2023, 1, 1)
    d2 = d1 + td
    assert d2 == datetime.date(2023, 1, 11)
    
    d3 = d2 - td
    assert d3 == d1
    
def test_locale_localeconv():
    """Test locale.localeconv() returns real C/POSIX values"""
    lc = locale.localeconv()
    assert lc['decimal_point'] == '.'
    assert lc['thousands_sep'] == ''
    assert lc['grouping'] == []

def test_locale_format_string():
    """Test locale.format_string"""
    # Test integer formatting with grouping
    result = locale.format_string('%d', 1234, grouping=True)
    assert result == '1234'  # No grouping in C locale
    
    # Test float formatting
    result = locale.format_string('%.2f', 123.456)
    assert result == '123.46'

def test_time_strftime():
    """Test time.strftime with real clock"""
    # Get current time
    t = time.localtime()
    formatted = time.strftime('%d%b%y', t)
    assert len(formatted) == 7  # DD (2) + MMM (3) + YY (2)

def test_time_localtime_gmtime():
    """Test time.localtime() and time.gmtime() coherence with real clock"""
    # Both should be close to each other
    lt = time.localtime()
    gt = time.gmtime()
    
    # Check that they represent the same moment
    # Difference should be timezone offset
    assert abs(lt.tm_sec - gt.tm_sec) <= 1

def test_multiple_import():
    """Test import multiple in one line"""
    import time, os.path
    assert hasattr(time, 'strftime')
    assert hasattr(os.path, 'exists')
