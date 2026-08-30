"""The Reasoning Twin — a deterministic (non-LLM) shadow that twins each structured reasoning
turn and checks the LLM caught the main points (docs/REASONING_2.0_DESIGN.md).

It enumerates the concerns that matter (from GINI's symbolic substrate — blackboard verdicts,
the predicate explainer, legality flags), asks the Reasoning persona to report coverage against
them, diffs that report EXACTLY (a set diff, no NLP), poses "why not X?" objections for silent
misses, and — after one bounded revision round — turns surviving objections into visible flags.
It is a challenger, never a judge: verdicts still come only from the deterministic oracle, and
with the Twin disabled every turn behaves exactly as before.
"""
from .contracts import Concern, Coverage, Objection, parse_coverage
from .dialectic import (
    COVERAGE_SCHEMA, Twin, TwinContext, TwinResult, concern_context, coverage_instruction,
)
from .aop import aop_concerns
from .course import course_concerns, current_lab_of
from .authoring import authoring_concerns
from .harness import GoldenTurn, HarnessReport, replay
from .justify import Adjudication, adjudicate, classify, state_holds
from .learner import learner_concerns
from .mission import mission_concerns
from .os_coach import coach_concerns, fallback_text, focus_line
from .salience import MAX_CONCERNS, MUST_ADDRESS, cap

__all__ = ["Concern", "Coverage", "Objection", "parse_coverage", "Twin", "TwinContext",
           "TwinResult", "concern_context", "coverage_instruction", "COVERAGE_SCHEMA",
           "Adjudication", "adjudicate", "classify", "state_holds", "mission_concerns",
           "coach_concerns", "focus_line", "fallback_text", "authoring_concerns", "aop_concerns", "course_concerns", "current_lab_of",
           "learner_concerns", "GoldenTurn", "HarnessReport", "replay",
           "MAX_CONCERNS", "MUST_ADDRESS", "cap"]
