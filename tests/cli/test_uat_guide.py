"""The UAT guide (docs/uat/P0-uat.md) tells the truth about the app (plan AC15).

AC15 says the guide's commands run as written on your Mac. The commands that fetch real prices
cannot run in a test, but the rest can be checked without any network:

- every `pg` command the guide shows, in a code block or inline, is a real command, and every
  option it uses is an option of that command (the guide cannot drift from the CLI unnoticed);
- the example files the guide shows (the CSV header, the confirmed holdings, the mapping file, the
  manual price file and the manual transaction rows) are read by the app.

Each test reads the guide itself, so a change to the guide that breaks a command or an example
fails here.
"""

import re
import shlex
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from playground.cli.main import app
from playground.importer.confirmed_csv import parse_confirmed_csv
from playground.importer.manual_csv import parse_manual_csv
from playground.importer.tr.csv_profiles import TR_CSV_SYNTHETIC_V1
from playground.marketdata.manual_prices import load_price_file

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "uat" / "P0-uat.md"

runner = CliRunner()


def _guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _blocks(language: str) -> list[str]:
    """The bodies of the guide's fenced code blocks in `language`, indented ones included."""
    blocks: list[str] = []
    current: list[str] | None = None
    current_language = ""
    for line in _guide_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if current is None:
                current, current_language = [], stripped[3:]
            else:
                if current_language == language:
                    blocks.append("\n".join(current))
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def _shell_lines() -> list[str]:
    """Every command line of the guide's bash blocks. The bodies of heredocs are files, not commands."""
    lines: list[str] = []
    for block in _blocks("bash"):
        end_of_heredoc: str | None = None
        for line in block.splitlines():
            if end_of_heredoc is not None:
                if line.strip() == end_of_heredoc:
                    end_of_heredoc = None
                continue
            heredoc = re.search(r"<<\s*'?(\w+)'?\s*$", line)
            if heredoc:
                end_of_heredoc = heredoc.group(1)
            lines.append(line)
    return lines


def _pg_arguments(text: str) -> list[str] | None:
    """The words after `pg` in a command line or an inline code span, or None if it holds no pg command."""
    match = re.search(r"(?:^|\s)(?:uv run )?pg(?:\s+(?P<rest>.*))?$", text)
    if match is None or not match.group("rest"):
        return None
    rest = match.group("rest").split(" > ")[0]
    return shlex.split(rest)


def _pg_commands() -> list[str]:
    """Every pg command the guide shows: one per command line, and one per inline code span."""
    commands = []
    for line in _shell_lines():
        if "uv run pg" in line:
            commands.append(line[line.index("uv run pg") :])
    for span in re.findall(r"`([^`\n]+)`", _guide_text()):
        if span.startswith(("uv run pg ", "pg ")):
            commands.append(span)
    return commands


def _problems(command: str) -> list[str]:
    """What is wrong with one pg command as the guide writes it: an unknown command or option.

    Typer ships its own copy of click, so a `click.Group` check would never match: a group is
    recognised by its `commands`, a leaf command has none.
    """
    arguments = _pg_arguments(command)
    if arguments is None:
        return []
    current = typer.main.get_command(app)
    path: list[str] = []
    problems: list[str] = []
    index = 0
    while index < len(arguments):
        word = arguments[index]
        subcommands = getattr(current, "commands", None) or {}
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            options = {
                option: param
                for param in current.params
                for option in (*param.opts, *param.secondary_opts)
                if option.startswith("--")
            }
            if name not in options:
                problems.append(f"{command!r}: pg {' '.join(path)} has no option {name}")
            elif not getattr(options[name], "is_flag", False) and "=" not in word:
                index += 1  # this option takes a value: skip it
        elif word in subcommands:
            current = subcommands[word]
            path.append(word)
        index += 1
    is_group = bool(getattr(current, "commands", None))
    if is_group and not all(argument.startswith("--") for argument in arguments):
        problems.append(f"{command!r}: pg {' '.join(path)} is a group of commands, not a command")
    return problems


def test_the_guide_shows_pg_commands() -> None:
    # Guards the scanner itself: a guide with no commands found would pass the next test for nothing.
    commands = _pg_commands()
    assert len(commands) > 40
    assert any("review export" in command for command in commands)
    assert any("PG_TODAY" in line for line in _shell_lines())


def test_every_pg_command_in_the_guide_exists_with_its_options() -> None:
    problems = [problem for command in _pg_commands() for problem in _problems(command)]
    assert problems == []


def test_the_scanner_finds_an_unknown_command_and_an_unknown_option() -> None:
    assert _problems("uv run pg holdings --as-of 2024-06-07") == []
    assert _problems("uv run pg holdings --on 2024-06-07") != []
    assert _problems("uv run pg --http-replay some/dir prices fetch --from 2019-01-01") == []
    assert _problems("uv run pg review") != []
    assert _problems("pg review export <id> --anonymise --out file.txt") == []


def test_the_guide_shows_the_csv_header_the_profile_expects() -> None:
    header = ";".join(TR_CSV_SYNTHETIC_V1.header)
    assert header in _blocks("text")


def _text_block_starting_with(prefix: str) -> str:
    for block in _blocks("text"):
        if block.startswith(prefix):
            return block
    raise AssertionError(f"the guide has no text block that starts with {prefix!r}")


def test_the_confirmed_holdings_example_rows_are_read() -> None:
    header = "# decimal=,\nisin;quantity;as_of\n"
    rows = _text_block_starting_with("DE0007164600;3;")

    holdings = parse_confirmed_csv((header + rows + "\n").encode("utf-8"))

    assert [(holding.isin, str(holding.quantity)) for holding in holdings] == [
        ("DE0007164600", "3"),
        ("IE00B4L5Y983", "0.4534"),
    ]


def test_the_manual_price_example_is_read() -> None:
    example = _text_block_starting_with("date;data_symbol;close;currency;adjustment")

    series = load_price_file((example + "\n").encode("utf-8"))

    assert [(one.symbol, len(one.points)) for one in series] == [("ALV.DE", 2)]


def test_the_manual_transaction_example_is_read_without_a_review_item() -> None:
    example = _text_block_starting_with("date;time;type;isin;quantity;price;currency;amount_eur;fees_eur;tax_eur;note")
    rows = _text_block_starting_with("2024-01-15;10:05;buy;")

    result = parse_manual_csv((example + "\n" + rows + "\n").encode("utf-8"))

    assert result.review == []
    assert [transaction.type.value for transaction in result.transactions] == [
        "buy",
        "interest",
        "split",
        "transfer_in",
    ]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PG_TODAY", "2024-12-31")
    path = str(tmp_path / "data")
    assert runner.invoke(app, ["--data-dir", path, "init"]).exit_code == 0
    return path


def test_the_mapping_file_example_is_read(data_dir: str, tmp_path: Path) -> None:
    example = _text_block_starting_with("isin;data_source;data_symbol;currency;note")
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(example + "\n", encoding="utf-8")

    result = runner.invoke(app, ["--data-dir", data_dir, "instruments", "map", "--file", str(mapping)])

    assert result.exit_code == 0, result.output
    assert "Mapped 3 instruments" in result.output
