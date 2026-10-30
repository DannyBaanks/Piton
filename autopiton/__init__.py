"""
AutoPiton - Differential Fuzzer & Semantic Oracle for PITON

Architecture:
- ProgramGenerator: Generates Python programs (AST-based, template-based)
- CPythonOracle: Executes programs using CPython, captures all outputs
- PitonRunner: Executes programs using PITON (ADAPT, PARSE, MIR, BUILD, EXECUTE)
- DifferentialComparator: Compares results, classifies outcomes
- FailureMinimizer: Reduces divergent programs to minimal test cases
- CorpusManager: Manages test corpus, deduplicates, persists results

Contract:
- Never modify PITON semantics to improve metrics
- Always verify against real CPython execution
- Fail-closed: report divergence, don't hide it
"""

__version__ = "0.1.0"
