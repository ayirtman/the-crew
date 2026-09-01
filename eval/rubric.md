# Rubric

Written before the first measured run. Frozen at tag `eval-frozen`. Five dimensions, each 0, 1 or 2, scored by Batu after opening the app directory and reading the code and tests. Ten points maximum per run. The machine numbers (verify pass, tests, cost, wall-clock) are recorded separately and are not part of this score.

Score every run of both graphs on the same day, in random order, without looking at which graph produced it.

| # | Dimension | 0 | 1 | 2 |
|---|---|---|---|---|
| d1 | Does what the idea said | Built something else, or the core feature is missing | The feature is there but a must-have behavior from the idea is absent | Every behavior the idea implied is present |
| d2 | Tests test the behavior | Tests are absent, trivial, or assert on implementation details | Tests exist for some behaviors, or pass for the wrong reason | One honest test per behavior, and each would fail if the behavior broke |
| d3 | Code you would keep | Would rewrite from scratch | Would keep the shape and fix things | Would ship as is for a prototype |
| d4 | Scope discipline | Added features, dependencies or files nobody asked for | Minor extras | Exactly one page, one route, logic in lib, nothing more |
| d5 | Runs as a person would expect | Page errors, or the route returns nonsense on obvious input | Works on the happy path, breaks on an obvious edge | Works on the happy path and on the obvious bad input |

Record scores in `eval/scores.csv` as `run_id,d1,d2,d3,d4,d5,notes`. The notes column is for the one sentence you would say to the builder.
