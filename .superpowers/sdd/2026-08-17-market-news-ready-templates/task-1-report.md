# Task 1 report: workflow binding and profile completeness

## Scope

Added unit coverage for the complete shared market-news source set. The existing name-independent NEWS-01 integration assertion from commit `e6c234f` was preserved unchanged. No production code or `packages/workflow_engine/` files were modified.

## TDD evidence

### RED

The legacy display-name binding was replayed before the final green verification:

```text
$ pytest -q tests/integration/test_data_api_contract.py::test_market_news_bootstrap_binds_bcse_releases_to_the_shared_news_dataset
F                                                                        [100%]
E   StopIteration
1 failed
```

This confirms the old `news-01:` lookup no longer matches after the `new-news-01` migration.

### GREEN

The new profile contract test passed:

```text
$ pytest -q tests/unit/test_belarus_market_pack.py::test_every_market_news_source_has_a_shared_dataset_binding
.                                                                        [100%]
1 passed
```

The requested complete verification passed:

```text
$ pytest -q tests/unit/test_belarus_market_pack.py tests/integration/test_data_api_contract.py
...........................                                              [100%]
27 passed
```

## Change

`test_every_market_news_source_has_a_shared_dataset_binding` asserts exactly the 15 required source keys and that every generated profile binds to `market-news`.

