"""Prompt templates for the Review AI code review tool."""

from __future__ import annotations

import os

from typing import Any


#: File extensions mapped to the language name shown to the model.
LANGUAGE_BY_EXTENSION = {
    '.bash': 'bash',
    '.c': 'c',
    '.cc': 'c++',
    '.cfg': 'ini',
    '.cpp': 'c++',
    '.cs': 'c#',
    '.css': 'css',
    '.cxx': 'c++',
    '.go': 'go',
    '.h': 'c header',
    '.hpp': 'c++ header',
    '.html': 'html',
    '.ini': 'ini',
    '.java': 'java',
    '.js': 'javascript',
    '.json': 'json',
    '.jsx': 'javascript',
    '.kt': 'kotlin',
    '.less': 'less',
    '.md': 'markdown',
    '.php': 'php',
    '.proto': 'protobuf',
    '.py': 'python',
    '.rb': 'ruby',
    '.rs': 'rust',
    '.rst': 'restructuredtext',
    '.scss': 'scss',
    '.sh': 'bash',
    '.sql': 'sql',
    '.swift': 'swift',
    '.toml': 'toml',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.txt': 'text',
    '.xml': 'xml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
}


def get_language(path: str) -> str | None:
    """Return a language name for a file path, or ``None`` if unknown."""
    ext = os.path.splitext(path)[1].lower()

    return LANGUAGE_BY_EXTENSION.get(ext)


#: The supported output languages: key -> (form label, description for the
#: model). The key is what the ``language`` tool option stores.
OUTPUT_LANGUAGES = {
    'chinese': ('Chinese', 'Simplified Chinese (简体中文)'),
    'english': ('English', 'English'),
}

#: The output language used when the tool settings do not specify one.
DEFAULT_OUTPUT_LANGUAGE = 'chinese'


def get_output_language_choices():
    """Return ``(key, label)`` tuples for a Django ChoiceField."""
    return [
        (key, label)
        for key, (label, _description) in OUTPUT_LANGUAGES.items()
    ]


def resolve_output_language(language: str | None) -> str:
    """Return a supported output language key, defaulting to Chinese."""
    value = str(language or '').strip().lower()

    if value in OUTPUT_LANGUAGES:
        return value

    return DEFAULT_OUTPUT_LANGUAGE


def build_system_prompt(
    max_findings_per_file: int,
    extra_instructions: str | None = None,
    language: str | None = None,
) -> str:
    """Build the system prompt sent with every request.

    ``language`` selects the output language for the generated text; it
    defaults to :py:data:`DEFAULT_OUTPUT_LANGUAGE`.
    """
    prompt = """\
You are a senior software engineer performing an automated code review inside \
a review system. You will be given the current contents of the files changed \
in a code change, with line numbers in the gutter.

Lines marked with ">" were added or modified by this change. Lines marked \
with " " are unchanged context.

Find real defects: logic errors, crash risks, race conditions, resource \
leaks, security vulnerabilities, data loss, off-by-one errors, broken error \
handling, and misuse of APIs. Also flag code that is clearly inconsistent \
with the surrounding code in a way that suggests a mistake.

Do not report: subjective style preferences, naming opinions, missing \
documentation, or anything you cannot tie to a specific line.

Only report a finding when it is located on a line marked with ">". Never \
invent line numbers; use the number printed in the gutter exactly as-is.

Respond with a single valid JSON object and nothing else (no markdown code \
fences, no prose before or after). The JSON object must have this shape:

{
  "summary": "one or two sentences describing the overall result",
  "findings": [
    {
      "file": "the/path/exactly/as/shown/in/the/File/heading",
      "line": 42,
      "severity": "high",
      "title": "short specific title",
      "message": "why this is a problem and what the impact is",
      "suggestion": "optional replacement code for the affected lines"
    }
  ]
}

Rules for the JSON:
- "severity" must be one of: low, medium, high, critical.
- "line" must be an integer, not a string.
- "file" must match a file path that appeared in the input exactly.
- Report at most %(max_findings)d findings per file. Prefer the most \
serious ones.
- "message" must be specific and actionable, at most four sentences.
- "suggestion" is optional. When provided, it must be the full replacement \
code for the affected line or lines, without line numbers or diff markers.
- If you find nothing worth reporting, return an empty "findings" array.
""" % {
        'max_findings': max_findings_per_file,
    }

    language = resolve_output_language(language)

    prompt += (
        '\nWrite the "summary", "title" and "message" values in %s. Keep the '
        'JSON keys, the "severity" values and the file paths exactly as '
        'specified above.\n' % OUTPUT_LANGUAGES[language][1])

    if extra_instructions:
        prompt += (
            '\nAdditional project-specific instructions from the user '
            '(these take precedence over the general guidance above, but '
            'must not change the JSON output format):\n\n%s\n'
            % extra_instructions.strip())

    return prompt


def build_user_prompt(
    files: list[dict[str, Any]],
    review_request_id: int,
    diff_revision: int,
    repository_name: str | None = None,
) -> str:
    """Build the user prompt containing the files to review.

    Each entry in ``files`` carries ``path``, ``body`` (the file contents with
    line numbers and change markers), ``total_lines``, ``included_lines`` and
    ``truncated``.
    """
    parts = [
        'Review request #%d, diff revision %d.'
        % (review_request_id, diff_revision),
        '',
    ]

    if repository_name:
        parts += [
            'Repository: %s' % repository_name,
            '',
        ]

    parts += [
        '%d file(s) follow. Each line is prefixed with a change marker and a '
        'line number.' % len(files),
        '',
    ]

    for file_info in files:
        path = file_info['path']
        language = get_language(path)
        header = '### File: %s' % path

        if language:
            header += ' (%s)' % language

        parts.append(header)

        if file_info.get('truncated'):
            parts.append(
                '(only the first %d of %d lines are shown)'
                % (file_info['included_lines'], file_info['total_lines']))

        parts += [
            file_info['body'],
            '',
            '### End of file: %s' % path,
            '',
        ]

    return '\n'.join(parts)
