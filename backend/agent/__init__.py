"""
Trip editing agent: change a saved trip from a plain-language request.

    tools.py    the state being edited (EditSession) and the six tools on it
    loop.py     the model loop: ask, run the tool calls, feed results back
    service.py  what happens after the loop: re-route, rewrite, attach extras

The model only ever chooses among tools. Which stops exist, where they are and
when they are visited stay with the server, exactly as in the main pipeline.
"""
