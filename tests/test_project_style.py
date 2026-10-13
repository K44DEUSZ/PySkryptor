import io
import tokenize
from pathlib import Path


def test_python_blocks_use_two_spaces_without_tabs():
  root = Path(__file__).resolve().parents[1]
  paths = (
    list((root / 'app').rglob('*.py'))
    + list((root / 'tests').rglob('*.py'))
    + list((root / 'tools').rglob('*.py'))
    + list(root.glob('*.spec'))
  )
  for path in paths:
    source = path.read_text(encoding='utf-8')
    indent_widths = [0]
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
      if token.type == tokenize.INDENT:
        assert '\t' not in token.string, f'{path}:{token.start[0]}'
        width = len(token.string)
        assert width == indent_widths[-1] + 2, f'{path}:{token.start[0]}'
        indent_widths.append(width)
      elif token.type == tokenize.DEDENT:
        indent_widths.pop()
