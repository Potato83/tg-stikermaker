from sticker_maker.cli import _normalize_argv, _parser


def test_legacy_positional_invocation_becomes_convert() -> None:
    assert _normalize_argv(["gifs", "output", "--quality", "best"]) == [
        "convert",
        "gifs",
        "output",
        "--quality",
        "best",
    ]


def test_no_arguments_uses_convert_defaults() -> None:
    args = _parser().parse_args(_normalize_argv([]))
    assert args.command == "convert"
    assert str(args.input) == "gifs"
    assert str(args.output) == "output"


def test_explicit_verify_is_unchanged() -> None:
    assert _normalize_argv(["verify", "output"]) == ["verify", "output"]
