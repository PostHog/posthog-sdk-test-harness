"""Assertions shared by the contract and Gherkin runners."""


def assert_request_count(requests, expected):
    actual = len(requests)
    if actual != expected:
        raise AssertionError(f"Expected {expected} requests, got {actual}")
