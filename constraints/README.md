# Constraints

Use the file matching the interpreter when installing the bot. Snapshots are provided for Python 3.12, 3.13, and 3.14, for example:

```sh
python3 -m pip install -c constraints/python314.txt -e .
```

`envs-xmpp` is intentionally pinned exactly while the project dependency
uses the compatible `>=1.5.1,<2.0` range.
