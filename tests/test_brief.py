

def test_the_bare_invocation_runs_the_brief():
    """Two scheduled routines are instructed to run this module bare. With a
    required subcommand that exited 2 with a usage message and no analysis --
    silently, because the routines piped it. The form people actually type has
    to be the form that works."""
    from hf_trading_bot.brief import main
    assert main([]) == 0


def test_the_bare_invocation_matches_the_explicit_one(capsys):
    """`brief` and bare must not drift into two different reports."""
    from hf_trading_bot.brief import main
    main([]); bare = capsys.readouterr().out
    main(["brief"]); explicit = capsys.readouterr().out
    assert bare == explicit and len(bare) > 200
