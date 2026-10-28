#!/usr/bin/env python3
"""Debug: test the login text patterns."""
import re
from sitescrape.check import _LOGIN_TEXT_PATTERNS

tests = ["Log In", "log in", "Login", "Log-in", "Sign In", "Sign in", "sign-in", "SignIn", 
         "Log Out", "Log out", "LogOut", "sign up", "sign-up", "Sign Up", "authenticate"]

for text in tests:
    matches = [p for p in _LOGIN_TEXT_PATTERNS if re.search(p, text)]
    print(f"{text:20s} -> matches: {bool(matches)}  patterns: {[p for p in matches]}")
