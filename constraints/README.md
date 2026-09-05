# Constraints

Use the file matching the interpreter when installing the bot, for example:

```sh
python3 -m pip install -c constraints/python313.txt -e .
```

`envs-xmpp` is intentionally pinned exactly while the project dependency
uses the compatible `>=0.1.2,<0.2` range.
