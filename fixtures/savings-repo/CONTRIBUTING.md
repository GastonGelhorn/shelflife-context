# Contributing

- Python 3.11+, standard library only. Nothing to install.
- Run the whole suite before you push: `python3 -m unittest discover -s tests -t .`
- Tests use `tests.helpers.temp_api()`, which points the service at a throwaway data folder.
  Never let a test write under `data/`.
- Keep modules small and the dependency direction one way: `api` → everything else; `money` → nothing.
- Commit messages say what the change does, in the present tense.
