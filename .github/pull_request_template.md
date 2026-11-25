## Summary

<!-- Briefly describe what changed and why. -->

## Related issue

<!-- Link the issue, e.g. Fixes #123, or write "N/A". -->

## Type of change

- [ ] Bug fix
- [ ] New edition / extractor
- [ ] Extraction improvement
- [ ] Documentation / tooling
- [ ] Other

## Verification

<!-- Describe how you verified the change. Include exact commands where possible. -->

- [ ] `python -m unittest discover -s tests`
- [ ] `python relikt.py -e <edition> verify` (if applicable)
- [ ] `python relikt.py -e <edition> full` / `status` (if applicable)
- [ ] Other:

## Checklist

- [ ] I did not add or modify files under `data/`.
- [ ] I did not commit generated content from `out/` or `tmp/`.
- [ ] I reviewed any new or changed code under `tools/<edition>/`; extractor modules are imported and executed by `relikt.py`, including during `verify`.
- [ ] Extracted/article text remains UTF-8 (Polish characters preserved) where applicable.
- [ ] No passwords, keys, tokens, or other sensitive data are included.
- [ ] Documentation was updated if behavior, commands, or layout changed.
