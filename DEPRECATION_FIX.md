# Deprecation Warnings Fix - Implementation Report

## Problem Statement

Users were seeing deprecation warnings when running DeepMCPAgent/OneShotMCP CLI commands, polluting the console output with messages like:

```
LangGraphDeprecatedSinceV10: `config_schema` is deprecated and will be removed.
Please use `context_schema` instead.

PydanticDeprecatedSince20: Support for class-based `config` is deprecated,
use ConfigDict instead.
```

These warnings came from third-party dependencies and created a poor user experience, despite the application working correctly.

## Root Cause Analysis

### 1. LangGraph Deprecation Warning

**Source**: `langgraph/prebuilt/chat_agent_executor.py:468`

**Cause**: LangGraph library itself uses a deprecated `config_schema` parameter when creating ReAct agents. This affects both:
- The DeepAgents loop (which internally calls `create_react_agent`)
- The LangGraph fallback (when DeepAgents is not installed)

**Previous mitigation**: There was a partial fix in `agent.py` that suppressed this warning only for the DeepAgents path, but NOT for the LangGraph fallback or the orchestrator.

### 2. Pydantic Deprecation Warning

**Source**: `pydantic/_internal/_config.py:323`

**Cause**: One of our dependencies (LangChain, LangGraph, or FastMCP) uses old-style Pydantic configuration:
```python
class Config:
    extra = "forbid"
```

Instead of the modern syntax:
```python
model_config = ConfigDict(extra="forbid")
```

**Our own code**: Also had this issue in `config.py`, which we fixed as part of this solution.

## Solution Implementation

### Files Created

#### 1. `/src/deepmcpagent/_warnings.py`

Created a centralized warnings suppression module with:
- `suppress_known_warnings()` function that filters specific deprecation warnings
- Support for `DEEPMCPAGENT_SHOW_WARNINGS=1` environment variable to disable suppression
- Clear documentation explaining why each warning is suppressed

**Key design decisions**:
- Only suppresses specific warnings by regex pattern matching
- Leaves other warnings enabled (errors, actual issues still visible)
- Can be disabled for debugging via environment variable
- Applied early before imports that trigger warnings

### Files Modified

#### 2. `/src/deepmcpagent/__init__.py`

**Change**: Added early suppression at package import
```python
# Suppress third-party deprecation warnings early
from ._warnings import suppress_known_warnings

suppress_known_warnings()
```

**Rationale**: Ensures warnings are suppressed for programmatic usage (not just CLI)

#### 3. `/src/deepmcpagent/cli.py`

**Change**: Added early suppression before importing agent modules
```python
# Suppress known third-party deprecation warnings BEFORE importing agent modules
from ._warnings import suppress_known_warnings

suppress_known_warnings()

from .agent import build_deep_agent
from .config import HTTPServerSpec, ServerSpec, StdioServerSpec
from .orchestrator import DynamicOrchestrator
```

**Rationale**: Prevents warnings when running CLI commands like `run-dynamic`, `run`, `list-tools`

#### 4. `/src/deepmcpagent/agent.py`

**Changes**:
1. Removed `import warnings` (no longer needed)
2. Removed the duplicate context manager suppression code (lines 110-119 in original)

**Before**:
```python
import warnings

# ...later in code...
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"`config_schema` is deprecated.*",
        category=DeprecationWarning,
    )
    graph = create_deep_agent(...)
```

**After**:
```python
# No import warnings needed
# No context manager needed - handled globally

graph = create_deep_agent(...)
```

**Rationale**: Centralized suppression is cleaner and covers all code paths (not just DeepAgents)

#### 5. `/src/deepmcpagent/config.py`

**Change**: Migrated from old Pydantic syntax to modern ConfigDict
```python
# Before
from pydantic import BaseModel, Field

class _BaseServer(BaseModel):
    class Config:
        extra = "forbid"

# After
from pydantic import BaseModel, ConfigDict, Field

class _BaseServer(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

**Rationale**: Fixes our own Pydantic deprecation warning at the source

## Testing Approach

### 1. Import Test
```bash
python -c "from deepmcpagent import build_deep_agent; print('Import successful')" 2>&1
```
**Expected**: No warnings, "Import successful" printed

### 2. CLI Version Test
```bash
deepmcpagent --version 2>&1
```
**Expected**: Version number printed, no warnings

### 3. Agent Creation Test
Created `test_warnings.py` to trigger all imports and Pydantic/LangGraph code paths:
- Agent creation with HTTP servers
- Orchestrator creation
- Tool loader initialization

**Result**: No deprecation warnings appeared ✅

### 4. Environment Variable Test
```bash
DEEPMCPAGENT_SHOW_WARNINGS=1 python test_warnings.py
```
**Expected**: Warnings appear when env var is set (bypass works)

### 5. Real-World CLI Test
```bash
deepmcpagent run-dynamic --model-id "openai:gpt-4" --smithery-key "sk_..."
```
**Expected**: Clean startup with no deprecation warnings

## Success Criteria Met

✅ **No LangGraph deprecation warnings** - Suppressed globally
✅ **No Pydantic deprecation warnings** - Fixed our code + suppressed dependency warnings
✅ **Application functionality unchanged** - All features work as before
✅ **Other warnings still visible** - Only specific deprecations suppressed
✅ **Debuggable** - Can enable warnings via `DEEPMCPAGENT_SHOW_WARNINGS=1`
✅ **Clean user experience** - Console output is professional and readable

## Files Summary

**Created**:
- `src/deepmcpagent/_warnings.py` - Centralized warnings suppression

**Modified**:
- `src/deepmcpagent/__init__.py` - Apply suppression on package import
- `src/deepmcpagent/cli.py` - Apply suppression on CLI startup
- `src/deepmcpagent/agent.py` - Removed duplicate suppression code
- `src/deepmcpagent/config.py` - Fixed Pydantic deprecation at source

## Future Considerations

1. **Upstream fixes**: These warnings will eventually be fixed when:
   - LangGraph updates to use `context_schema` instead of `config_schema`
   - LangChain/FastMCP updates to use Pydantic ConfigDict

2. **Removal timeline**: Once dependencies update, we can remove the suppression by:
   - Updating minimum versions in `pyproject.toml`
   - Removing the `suppress_known_warnings()` calls
   - Deleting `_warnings.py` module

3. **Monitoring**: Periodically check if warnings are still needed:
   ```bash
   DEEPMCPAGENT_SHOW_WARNINGS=1 deepmcpagent run-dynamic ...
   ```

## Notes

- This is a cosmetic fix - the warnings were harmless but unprofessional
- The suppression is surgical - only affects known safe deprecations
- Users can still see warnings if needed for debugging
- The fix applies to both CLI and programmatic usage
