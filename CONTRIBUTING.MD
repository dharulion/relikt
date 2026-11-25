### Contributing:

1. Fork the repository.
2. Create a branch with a descriptive name, e.g. `git checkout -b dragon6-extract`.
3. Add your changes (e.g. scripts for a new issue in `tools/[issue-name]/`) and stage/commit them with `git add <files>` and `git commit -m "Description of change"`.
4. Push the branch to your fork: `git push origin <branch-name>`.
5. Open a Pull Request and briefly describe what changed and how to verify it, e.g. `python relikt.py -e <issue> status`.

Do not add content from `data/`, `out/` and `tmp/` to your PR - these are source materials and extraction results, not project code.

Note: scripts in `tools/<edition>/` are imported and executed even by `relikt.py verify`. Only run code you trust and review new editions before using them.