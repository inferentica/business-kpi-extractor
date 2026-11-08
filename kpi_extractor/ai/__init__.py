"""Every AI task, its prompt and the schema its answer must pass.

The AI never supplies a number that gets stored. It chooses (which documents, sections, KPIs and rows matter) and it
points (a table cell, or a quote with the number as written); code reads every value from the document and checks it.

Every prompt shares one system message and puts the document first, so repeated tasks on the same document (the reasoning reader's
reading, review, audit and explanations) hit DeepSeek's context cache and cost a fraction of a fresh read.
"""
from __future__ import annotations

from .schemas import (  # noqa: F401 - the package's names, as when it was one module
    _check_key, _codes, _KEY, BreakdownName, Classification, Curation,
    Explanation, Explanations, for_period, GroupKind, GroupSpec, KpiSpec,
    Located, Locator, Maintenance, MAX_GROUPS, MAX_KPIS_PER_GROUP, MAX_LISTED_KPIS,
    MIN_KEPT_METRICS, MISSING_REASONS, normalize_spec, Review, REVIEW_REASONS, Selection,
    Spec, TableLocator, TagPicks, Unit)
from .calls import (  # noqa: F401 - the package's names, as when it was one module
    _strip_fences, AiResponseError, parse, parse_located, parse_review, parse_spec)
from .prompts.common import (  # noqa: F401 - the package's names, as when it was one module
    DOCUMENT_FORMAT, prompt, SYSTEM)
from .prompts.propose import (  # noqa: F401 - the package's names, as when it was one module
    PROPOSE_TASK)
from .prompts.maintain import (  # noqa: F401 - the package's names, as when it was one module
    MAINTAIN_TASK)
from .prompts.locate import (  # noqa: F401 - the package's names, as when it was one module
    LOCATE_TASK)
from .prompts.review import (  # noqa: F401 - the package's names, as when it was one module
    REVIEW_TASK)
from .prompts.classify import (  # noqa: F401 - the package's names, as when it was one module
    CLASSIFY_TASK)
from .prompts.tags import (  # noqa: F401 - the package's names, as when it was one module
    TAG_TASK)
from .prompts.select import (  # noqa: F401 - the package's names, as when it was one module
    SELECT_TASK)
from .prompts.curate import (  # noqa: F401 - the package's names, as when it was one module
    CURATE_TASK)
from .prompts.explain import (  # noqa: F401 - the package's names, as when it was one module
    EXPLAIN_TASK)
from .prompts import (  # noqa: F401 - the package's names, as when it was one module
    _TASKS, kpi_lines, locate_data, prompt_version, propose_data)
