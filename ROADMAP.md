# Apollo Settings Roadmap

This document outlines planned improvements for the Apollo Settings library, organized by priority level.

## Priority 1: Critical

### 1.1 Test Coverage

The project currently lacks any test suite. Comprehensive testing is essential for reliability and maintainability.

**Scope:**
- Unit tests for `ApolloClient` class
- Unit tests for `ApolloSettings` class
- Integration tests with mocked Apollo server responses
- Edge case coverage (network failures, invalid configurations, concurrent access)

**Recommended Stack:**
- `pytest` as the test framework
- `pytest-mock` for mocking
- `responses` or `respx` for HTTP request mocking
- `pytest-cov` for coverage reporting

**Target:** Minimum 80% code coverage

### 1.2 Thread Safety

The current implementation has potential race conditions in multi-threaded environments.

**Affected Areas:**
- `ApolloClient._configs` dictionary access and modification (`client.py:116-138`)
- `ApolloClient._subscribers` list sorting and iteration (`client.py:150-159`)
- `ApolloSettings._update_actions` list access (`core.py:167-173`)

**Solution:**
- Introduce `threading.Lock` or `threading.RLock` for shared state protection
- Consider using thread-safe data structures where appropriate

### 1.3 CI/CD Pipeline Updates

**Current State:**
- Only CD workflow exists (PyPI publishing)
- CD workflow uses `poetry-publish` action, but project has migrated to `uv`

**Required Changes:**
- Add CI workflow for pull requests and main branch pushes:
  - Run `ruff` linter and formatter checks
  - Run `pyright` type checking
  - Run test suite
- Update CD workflow to use `uv` for building and publishing

---

## Priority 2: High

### 2.1 Error Handling and Retry Mechanism

The current error handling in long-polling is insufficient (`client.py:233-234`).

**Current Behavior:**
```python
except Exception:
    logger.exception('long polling failed')
```

**Improvements:**
- Implement exponential backoff for transient failures
- Distinguish between recoverable and non-recoverable errors
- Add configurable maximum retry attempts
- Add circuit breaker pattern for repeated failures

### 2.2 Resource Management

The library lacks proper resource cleanup mechanisms.

**Issues:**
- No `close()` method on `ApolloSettings` or `ApolloClient`
- Background polling thread may leak if not properly stopped
- No context manager support for automatic cleanup

**Solution:**
- Implement `close()` method for graceful shutdown
- Add `__enter__` and `__exit__` for context manager support
- Consider weak references for subscriber management
- Implement `__del__` as a safety net (with appropriate warnings)

### 2.3 Type Coercion

Configuration values from Apollo are always strings, which may cause type issues.

**Current Behavior (`core.py:166`):**
```python
setattr(self, field, apollo_value.value)  # value is always str
```

**Considerations:**
- Verify that Pydantic's `validate_assignment=True` handles coercion correctly
- Add explicit type conversion based on field annotations
- Document expected behavior for complex types

---

## Priority 3: Medium

### 3.1 Health Check and Observability

No API exists to expose client health status.

**Proposed Interface:**
```python
class ApolloClient:
    @property
    def is_healthy(self) -> bool: ...

    @property
    def last_successful_poll(self) -> datetime | None: ...

    @property
    def consecutive_failures(self) -> int: ...

    def get_status(self) -> ApolloClientStatus: ...
```

### 3.2 Configuration Deletion Detection

The current implementation only detects value changes, not key deletions.

**Scope:**
- Track keys that exist in settings but are removed from Apollo
- Provide callback mechanism for deleted configurations
- Consider reset-to-default behavior for deleted keys

### 3.3 Async Client Support

Provide an alternative async implementation for async-first applications.

**Scope:**
- `AsyncApolloClient` using `httpx` or `aiohttp`
- `AsyncApolloSettings` base class
- Maintain API parity with synchronous implementation

**Considerations:**
- This should be optional to avoid adding async dependencies for sync users
- Consider using `asyncio.Task` instead of threading for async variant

---

## Priority 4: Low

### 4.1 Documentation Enhancements

**Additions:**
- `examples/` directory with practical usage examples
- API reference documentation (using `mkdocs` + `mkdocstrings`)
- `CONTRIBUTING.md` with development guidelines
- Detailed configuration reference in README

### 4.2 Singleton Pattern for ApolloClient

Consider providing an optional singleton pattern for `ApolloClient` to simplify usage in applications with multiple settings classes.

### 4.3 Metrics and Logging Improvements

**Scope:**
- Structured logging with configurable levels
- Optional metrics export (Prometheus, StatsD)
- Request/response timing information

### 4.4 Configuration Validation

Enhanced validation for Apollo-specific configurations:
- URL format validation for `meta_url`
- Namespace naming convention validation
- Connection testing on initialization (optional)

---

## Implementation Notes

- Each improvement should be implemented in a separate pull request
- Breaking changes require major version bump following semantic versioning
- All new features must include corresponding tests and documentation updates
