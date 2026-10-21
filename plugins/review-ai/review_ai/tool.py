"""Review Bot tool that reviews code changes using an AI model.

DeepSeek is just one of the supported providers; see
:py:mod:`review_ai.providers` for the full list.
"""

from __future__ import annotations

import json

from collections import Counter
from collections.abc import Sequence
from typing import Any

from reviewbot.tools.base import BaseTool

from review_ai.client import (BaseChatClient, ChatError, DEFAULT_TIMEOUT,
                              create_chat_client)
from review_ai.prompts import (DEFAULT_OUTPUT_LANGUAGE,
                               build_system_prompt,
                               build_user_prompt,
                               get_output_language_choices,
                               resolve_output_language)
from review_ai.providers import (DEFAULT_PROVIDER,
                                 get_provider,
                                 get_provider_choices,
                                 resolve_api_key,
                                 resolve_base_url,
                                 resolve_model)


#: The default token budget for a single model response.
DEFAULT_MAX_OUTPUT_TOKENS = 32768

#: The severities we understand, from least to most serious.
SEVERITIES = ('low', 'medium', 'high', 'critical')

#: Mapping of severity names to their sort order.
SEVERITY_RANK = {
    'low': 1,
    'medium': 2,
    'high': 3,
    'critical': 4,
}

#: The change marker used for lines touched by the diff.
CHANGED_MARKER = '>'

#: The change marker used for unchanged context lines.
CONTEXT_MARKER = ' '

#: The maximum number of characters we allow for a single finding title.
MAX_TITLE_LEN = 120

#: Localized severity labels for display. The API and the review metadata keep
#: using the English keys in :py:data:`SEVERITIES`.
SEVERITY_LABELS: dict[str, dict[str, str]] = {
    'chinese': {
        'low': '低',
        'medium': '中',
        'high': '高',
        'critical': '严重',
    },
    'english': {
        'low': 'low',
        'medium': 'medium',
        'high': 'high',
        'critical': 'critical',
    },
}

#: Localized report text, keyed by output language.
REPORT_TEXTS: dict[str, dict[str, str]] = {
    'chinese': {
        'reviewed': 'AI-Reviewer 审查了 %(files)d 个文件，发现 %(findings)d 个问题',
        'clean': 'AI-Reviewer 审查了 %(files)d 个文件，未发现问题',
        'details': '（%(details)s）',
        'period': '。',
        'skipped': '跳过 %(skipped)d 个文件，丢弃 %(dropped)d 条结果。',
        'skipped_notice': ('注意：本次只审查了 %(files)d 个文件，另有 '
                           '%(skipped)d 个文件因 max_files 限制未审查。'),
        'unparsed': '模型输出有 %(unparsed)d 次无法解析，相关改动未被审查。',
        'suggestion': '建议修复：',
    },
    'english': {
        'reviewed': ('AI-Reviewer reviewed %(files)d file(s) and reported '
                     '%(findings)d finding(s)'),
        'clean': ('AI-Reviewer reviewed %(files)d file(s) and reported no '
                  'findings'),
        'details': ' (%(details)s)',
        'period': '.',
        'skipped': '%(skipped)d file(s) skipped, %(dropped)d finding(s) '
                   'dropped.',
        'skipped_notice': ('Note: only %(files)d file(s) were reviewed. '
                           '%(skipped)d file(s) were skipped due to the '
                           'max_files limit.'),
        'unparsed': ('The model output for %(unparsed)d request(s) could not '
                     'be parsed; those changes were not reviewed.'),
        'suggestion': 'Suggested fix:',
    },
}


class ReviewAITool(BaseTool):
    """Review Bot tool that reviews code changes using an AI model."""

    name = 'AI-Reviewer'
    version = '1.0'
    description = (
        'Reviews code changes using an AI model (DeepSeek, OpenAI, Qwen, '
        'Kimi, GLM, Claude, Ollama, or any OpenAI-compatible endpoint), '
        'reporting likely bugs and security issues as inline comments.'
    )

    # AI reviews of a large change can take a while.
    timeout = 600

    options = [
        {
            'name': 'provider',
            'field_type': 'django.forms.ChoiceField',
            'default': DEFAULT_PROVIDER,
            'field_options': {
                'label': 'Provider',
                'help_text': (
                    'The model provider to call. Use "custom" with a base '
                    'URL for any other OpenAI-compatible endpoint (vLLM, '
                    'one-api, a corporate gateway, etc.).'
                ),
                'choices': get_provider_choices(),
                'required': True,
            },
        },
        {
            'name': 'api_key',
            'field_type': 'django.forms.CharField',
            'default': '',
            'field_options': {
                'label': 'API key',
                'help_text': (
                    'Leave this empty and set REVIEW_AI_API_KEY (or the '
                    'provider-specific variable, such as DEEPSEEK_API_KEY or '
                    'OPENAI_API_KEY) on the worker instead, so the key is '
                    'never stored in the database.'
                ),
                'required': False,
            },
            'widget': {
                'type': 'django.forms.PasswordInput',
                'attrs': {
                    'autocomplete': 'off',
                },
            },
        },
        {
            'name': 'model',
            'field_type': 'django.forms.CharField',
            'default': '',
            'field_options': {
                'label': 'Model',
                'help_text': (
                    'Leave empty to use the provider default (for example '
                    '"deepseek-flash" for DeepSeek).'
                ),
                'required': False,
            },
        },
        {
            'name': 'base_url',
            'field_type': 'django.forms.CharField',
            'default': '',
            'field_options': {
                'label': 'API base URL',
                'help_text': (
                    'Leave empty to use the provider default. Set this to '
                    'route through a proxy, a private gateway, or a '
                    'self-hosted endpoint.'
                ),
                'required': False,
            },
        },
        {
            'name': 'min_severity',
            'field_type': 'django.forms.ChoiceField',
            'default': 'low',
            'field_options': {
                'label': 'Minimum severity',
                'help_text': (
                    'Findings below this severity are discarded. Use '
                    '"medium" or "high" to reduce noise.'
                ),
                'choices': [
                    ('low', 'Low'),
                    ('medium', 'Medium'),
                    ('high', 'High'),
                    ('critical', 'Critical'),
                ],
                'required': True,
            },
        },
        {
            'name': 'language',
            'field_type': 'django.forms.ChoiceField',
            'default': DEFAULT_OUTPUT_LANGUAGE,
            'field_options': {
                'label': 'Output language',
                'help_text': (
                    'The language used for the review summary and comments. '
                    'Defaults to Chinese.'
                ),
                'choices': get_output_language_choices(),
                'required': True,
            },
        },
        {
            'name': 'max_findings_per_file',
            'field_type': 'django.forms.IntegerField',
            'default': 5,
            'field_options': {
                'label': 'Maximum findings per file',
                'help_text': (
                    'The maximum number of comments to post for a single '
                    'file. Extra findings are dropped.'
                ),
                'required': True,
            },
        },
        {
            'name': 'max_files',
            'field_type': 'django.forms.IntegerField',
            'default': 20,
            'field_options': {
                'label': 'Maximum files',
                'help_text': (
                    'The maximum number of changed files to review. Extra '
                    'files are skipped, which keeps cost predictable on '
                    'large changes.'
                ),
                'required': True,
            },
        },
        {
            'name': 'max_file_lines',
            'field_type': 'django.forms.IntegerField',
            'default': 1500,
            'field_options': {
                'label': 'Maximum lines per file',
                'help_text': (
                    'Only the first N lines of each file are sent to the '
                    'model. Increase this if your files are large and you '
                    'want the whole file reviewed.'
                ),
                'required': True,
            },
        },
        {
            'name': 'max_prompt_chars',
            'field_type': 'django.forms.IntegerField',
            'default': 48000,
            'field_options': {
                'label': 'Maximum characters per request',
                'help_text': (
                    'Files are grouped into requests that stay under this '
                    'size. Roughly 4 characters equal one token.'
                ),
                'required': True,
            },
        },
        {
            'name': 'max_output_tokens',
            'field_type': 'django.forms.IntegerField',
            'default': DEFAULT_MAX_OUTPUT_TOKENS,
            'field_options': {
                'label': 'Maximum output tokens',
                'help_text': (
                    'The token budget for a single response. Reasoning '
                    'models (such as deepseek-flash) '
                    'spend part of this budget on their chain of thought, so '
                    'keep this comfortably above the size of the JSON you '
                    'expect back.'
                ),
                'required': True,
            },
        },
        {
            'name': 'request_timeout',
            'field_type': 'django.forms.IntegerField',
            'default': int(DEFAULT_TIMEOUT),
            'field_options': {
                'label': 'Request timeout (seconds)',
                'help_text': (
                    'How long to wait for a single AI API response '
                    'before failing. The tool-level timeout still applies.'
                ),
                'required': True,
            },
        },
        {
            'name': 'extra_instructions',
            'field_type': 'django.forms.CharField',
            'default': '',
            'field_options': {
                'label': 'Extra instructions',
                'help_text': (
                    'Optional project-specific guidance for the model, '
                    'such as conventions or things it should ignore.'
                ),
                'required': False,
            },
            'widget': {
                'type': 'django.forms.Textarea',
                'attrs': {
                    'rows': 4,
                    'cols': 80,
                },
            },
        },
    ]

    def handle_files(
        self,
        files: Sequence[Any],
        **kwargs: Any,
    ) -> None:
        """Review all changed files using the configured AI provider.

        ``**kwargs`` are the ones passed to
        :py:meth:`~reviewbot.tools.base.tool.BaseTool.execute`.
        """
        settings = self.settings
        review = kwargs.get('review')

        if review is None and files:
            review = files[0].review

        max_files = self._get_int('max_files', 20)
        max_file_lines = self._get_int('max_file_lines', 1500)
        max_prompt_chars = self._get_int('max_prompt_chars', 48000)
        max_findings = max(1, self._get_int('max_findings_per_file', 5))
        max_output_tokens = max(
            256,
            self._get_int('max_output_tokens', DEFAULT_MAX_OUTPUT_TOKENS))
        min_severity = self._get_min_severity()
        language = self._get_output_language()
        texts = REPORT_TEXTS[language]

        entries, skipped = self._collect_files(
            files=files,
            max_files=max_files,
            max_file_lines=max_file_lines,
            **kwargs)

        if not entries:
            self.output = (
                'No reviewable files found (%d file(s) skipped: empty, '
                'binary, deleted, or unsupported).\n'
                % skipped)

            self.logger.info('AI-Reviewer: no reviewable files (skipped=%d)',
                             skipped)

            return

        client = self._create_client()

        system_prompt = build_system_prompt(
            max_findings_per_file=max_findings,
            extra_instructions=settings.get('extra_instructions') or None,
            language=language)

        review_request_id = getattr(review, 'review_request_id', 0) or 0
        diff_revision = getattr(review, 'diff_revision', 0) or 0
        repository_name = self._get_repository_name(kwargs)

        batches = self._make_batches(entries=entries,
                                     max_chars=max_prompt_chars)

        provider = client.provider
        client_label = '%s (%s)' % (provider.label, client.model)

        self.logger.info('AI-Reviewer: reviewing %d file(s) in %d request(s) '
                         '(provider=%s, skipped=%d)',
                         len(entries), len(batches), client_label, skipped)

        output_lines = [
            'AI-Reviewer report',
            '==================',
            'Provider: %s' % provider.label,
            'Model: %s' % client.model,
            'Files reviewed: %d' % len(entries),
            'Files skipped: %d' % skipped,
            'Requests: %d' % len(batches),
            '',
        ]

        total_findings = 0
        severity_counts: Counter[str] = Counter()
        dropped = 0
        unparsed = 0

        for batch_i, batch in enumerate(batches, start=1):
            user_prompt = build_user_prompt(
                files=batch,
                review_request_id=review_request_id,
                diff_revision=diff_revision,
                repository_name=repository_name)

            self.logger.debug('AI-Reviewer: sending request %d/%d (%d chars, '
                              '%d file(s))',
                              batch_i, len(batches), len(user_prompt),
                              len(batch))

            try:
                raw_response = client.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_output_tokens)
            except ChatError as e:
                self.output = '\n'.join(output_lines + [
                    '',
                    'FAILED on request %d/%d: %s' % (batch_i, len(batches), e),
                ])

                raise

            findings, parse_warnings = self._parse_response(raw_response)

            if parse_warnings and not findings and raw_response.strip():
                unparsed += 1

            output_lines += [
                '--- Request %d/%d ---' % (batch_i, len(batches)),
                'Files: %s' % ', '.join(f['path'] for f in batch),
                'JSON parse warnings: %s' % (
                    '; '.join(parse_warnings) if parse_warnings else 'none'),
                'Findings returned: %d' % len(findings),
                '',
            ]

            applied, dropped_here = self._apply_findings(
                batch=batch,
                findings=findings,
                min_severity=min_severity,
                max_findings=max_findings,
                review=review,
                language=language)

            dropped += dropped_here

            for finding in applied:
                total_findings += 1
                severity_counts[finding['severity']] += 1

                output_lines.append(
                    '  [%s] %s:%d %s'
                    % (finding['severity'], finding['file'],
                       finding['line'], finding['title']))

        output_lines += [
            '',
            'Total findings posted: %d' % total_findings,
            'Findings dropped (severity filter, duplicates, or limits): %d'
            % dropped,
        ]

        self.output = '\n'.join(output_lines)

        if review is not None:
            if total_findings:
                summary = texts['reviewed'] % {
                    'files': len(entries),
                    'findings': total_findings,
                }

                details = ', '.join(
                    '%d %s' % (severity_counts[severity],
                               SEVERITY_LABELS[language][severity])
                    for severity in reversed(SEVERITIES)
                    if severity_counts[severity])

                if details:
                    summary += texts['details'] % {'details': details}
            else:
                summary = texts['clean'] % {'files': len(entries)}

            summary += texts['period']

            # Skipped files usually mean the diff exceeded max_files, so the
            # warning has to be visible even when no findings were posted.
            if skipped or dropped:
                joiner = ' ' if language == 'english' else ''
                summary += joiner + texts['skipped'] % {
                    'skipped': skipped,
                    'dropped': dropped,
                }

            review.body_top = summary

        if review is not None and unparsed:
            warning = texts['unparsed'] % {'unparsed': unparsed}

            review.body_top = ('%s %s' % (review.body_top, warning)).strip()

        # Review Bot only publishes a review when it has comments, so a run
        # that skipped files without reporting anything would otherwise end up
        # as a clean "passed." with no hint that part of the change was never
        # reviewed. Post a notice so the skip stays visible.
        if review is not None and skipped and not total_findings and entries:
            notice = texts['skipped_notice'] % {
                'files': len(entries),
                'skipped': skipped,
            }

            try:
                entries[0]['file'].comment(notice,
                                           first_line=1,
                                           severity='low')
            except Exception as e:
                self.logger.exception('AI-Reviewer: could not post the skipped '
                                      'files notice: %s', e)

        self.logger.info('AI-Reviewer: posted %d finding(s), dropped %d '
                         '(skipped files=%d)',
                         total_findings, dropped, skipped)

    ######################################################################
    # Data collection
    ######################################################################

    def _collect_files(
        self,
        files: Sequence[Any],
        max_files: int,
        max_file_lines: int,
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return the reviewable file entries and the number of skipped files.

        ``**kwargs`` are forwarded to
        :py:meth:`~reviewbot.tools.base.tool.BaseTool.get_can_handle_file`.
        """
        entries: list[dict[str, Any]] = []
        skipped = 0

        for f in files:
            if len(entries) >= max_files:
                skipped += 1
                continue

            if not self.get_can_handle_file(review_file=f, **kwargs):
                skipped += 1
                continue

            entry = self._build_file_entry(f=f, max_file_lines=max_file_lines)

            if entry is None:
                skipped += 1
                continue

            entries.append(entry)

        return entries, skipped

    def _build_file_entry(
        self,
        f: Any,
        max_file_lines: int,
    ) -> dict[str, Any] | None:
        """Return the prompt entry for a file, or ``None`` if unreviewable."""
        contents = f.patched_file_contents

        if contents is None:
            # Deleted file, or the server could not produce the file.
            return None

        if b'\x00' in contents[:8192]:
            # Binary content. Review Board normally filters these out, but a
            # null byte near the top means text analysis is meaningless.
            return None

        try:
            text = contents.decode('utf-8')
        except UnicodeDecodeError:
            text = contents.decode('utf-8', 'replace')

        if not text.strip():
            return None

        all_lines = text.splitlines()
        total_lines = len(all_lines)
        truncated = total_lines > max_file_lines

        if truncated:
            all_lines = all_lines[:max_file_lines]

        changed_lines = self._get_changed_lines(f)
        body_lines: list[str] = []

        for line_num, line in enumerate(all_lines, start=1):
            if line_num in changed_lines:
                marker = CHANGED_MARKER
            else:
                marker = CONTEXT_MARKER

            # Keep extremely long lines from blowing up the prompt. The
            # model does not need the tail of a minified file.
            body_lines.append('%s%5d | %s' % (marker, line_num, line[:2000]))

        return {
            'file': f,
            'path': f.dest_file,
            'body': '\n'.join(body_lines),
            'total_lines': total_lines,
            'included_lines': len(all_lines),
            'truncated': truncated,
            'chars': sum(len(line) for line in body_lines),
        }

    def _get_changed_lines(self, f: Any) -> set[int]:
        """Return the modified line numbers in the patched version of a file."""
        changed: set[int] = set()

        try:
            chunks = f.diff_data.chunks
        except AttributeError:
            return changed

        for chunk in chunks:
            if chunk.get('change') == 'equal':
                continue

            for row in chunk.get('lines') or []:
                try:
                    # Index 4 is the line number in the patched file (empty
                    # for deleted lines).
                    line_num = row[4]
                except (IndexError, TypeError):
                    continue

                if not line_num:
                    continue

                try:
                    changed.add(int(line_num))
                except (TypeError, ValueError):
                    continue

        return changed

    def _make_batches(
        self,
        entries: list[dict[str, Any]],
        max_chars: int,
    ) -> list[list[dict[str, Any]]]:
        """Split file entries into request-sized batches.

        A single file larger than ``max_chars`` gets a batch of its own.
        """
        max_chars = max(2000, max_chars)
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0

        for entry in entries:
            entry_chars = entry['chars']

            if current and current_chars + entry_chars > max_chars:
                batches.append(current)
                current = []
                current_chars = 0

            current.append(entry)
            current_chars += entry_chars

        if current:
            batches.append(current)

        return batches

    ######################################################################
    # Response handling
    ######################################################################

    def _parse_response(
        self,
        raw: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return the findings and parse warnings from a response."""
        warnings: list[str] = []
        data = self._extract_json(raw)

        if data is None:
            self.logger.error('AI-Reviewer: could not find a JSON object in the '
                              'response: %r', raw[:2000])

            return [], ['no JSON object found in response']

        findings = data.get('findings')

        if not isinstance(findings, list):
            warnings.append('"findings" is not a list')
            findings = []

        result: list[dict[str, Any]] = []

        for item in findings:
            if not isinstance(item, dict):
                warnings.append('finding is not an object')
                continue

            result.append(item)

        return result, warnings

    def _extract_json(self, raw: str) -> dict[str, Any] | None:
        """Extract the first JSON object from a response, or ``None``.

        Models sometimes wrap JSON in markdown fences or add a sentence in
        front of it, even when told not to.
        """
        decoder = json.JSONDecoder()
        text = raw.strip()

        for i, char in enumerate(text):
            if char != '{':
                continue

            try:
                obj, _end = decoder.raw_decode(text[i:])
            except ValueError:
                continue

            if isinstance(obj, dict):
                return obj

        return None

    def _apply_findings(
        self,
        batch: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        min_severity: str,
        max_findings: int,
        review: Any,
        language: str = DEFAULT_OUTPUT_LANGUAGE,
    ) -> tuple[list[dict[str, Any]], int]:
        """Post findings as comments on the review.

        Returns the applied findings and the number of findings dropped.
        """
        by_path = {
            self._normalize_path(entry['path']): entry
            for entry in batch
        }

        by_basename: dict[str, list[dict[str, Any]]] = {}

        for entry in batch:
            basename = entry['path'].rsplit('/', 1)[-1].lower()
            by_basename.setdefault(basename, []).append(entry)

        applied: list[dict[str, Any]] = []
        dropped = 0
        per_file: Counter[str] = Counter()
        seen: set[tuple[str, int, str]] = set()

        min_rank = SEVERITY_RANK[min_severity]

        for finding in findings:
            entry = self._resolve_entry(
                finding.get('file'),
                by_path=by_path,
                by_basename=by_basename)

            if entry is None:
                self.logger.warning('AI-Reviewer: ignoring finding for unknown '
                                    'file %r', finding.get('file'))
                dropped += 1
                continue

            severity = str(finding.get('severity') or 'medium').lower()

            if severity not in SEVERITY_RANK:
                severity = 'medium'

            if SEVERITY_RANK[severity] < min_rank:
                dropped += 1
                continue

            title = ' '.join(str(finding.get('title') or '').split())
            message = str(finding.get('message') or '').strip()

            if not message:
                message = title

            if not message:
                dropped += 1
                continue

            title = title[:MAX_TITLE_LEN] or 'AI finding'

            line = self._coerce_line(finding.get('line'))
            path = entry['path']

            if line is None or line < 1 or line > entry['included_lines']:
                self.logger.warning(
                    'AI-Reviewer: ignoring finding with out-of-range line %r '
                    'for %s (1-%d)',
                    finding.get('line'), path, entry['included_lines'])
                dropped += 1
                continue

            key = (path, line, message)

            if key in seen:
                dropped += 1
                continue

            seen.add(key)

            if per_file[path] >= max_findings:
                dropped += 1
                continue

            comment = self._format_comment(
                severity=severity,
                title=title,
                message=message,
                suggestion=finding.get('suggestion'),
                language=language)

            try:
                entry['file'].comment(comment,
                                      first_line=line,
                                      severity=severity)
            except Exception as e:
                self.logger.exception('AI-Reviewer: could not comment on %s:%d: '
                                      '%s', path, line, e)
                dropped += 1
                continue

            per_file[path] += 1

            applied.append({
                'file': path,
                'line': line,
                'severity': severity,
                'title': title,
            })

        return applied, dropped

    def _resolve_entry(
        self,
        name: Any,
        by_path: dict[str, dict[str, Any]],
        by_basename: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        """Resolve a model-reported file name to an entry, or ``None``."""
        if not name:
            return None

        key = self._normalize_path(str(name))

        entry = by_path.get(key)

        if entry is not None:
            return entry

        # Case-insensitive fallback (models sometimes change casing).
        lowered = key.lower()

        for path, candidate in by_path.items():
            if path.lower() == lowered:
                return candidate

        # Last resort: match on the file name alone, but only when it is
        # unambiguous within the batch.
        basename = key.rsplit('/', 1)[-1].lower()

        candidates = by_basename.get(basename) or []

        if len(candidates) == 1:
            return candidates[0]

        return None

    def _format_comment(
        self,
        severity: str,
        title: str,
        message: str,
        suggestion: Any,
        language: str = DEFAULT_OUTPUT_LANGUAGE,
    ) -> str:
        """Format a finding as comment text."""
        language = resolve_output_language(language)
        texts = REPORT_TEXTS[language]
        label = SEVERITY_LABELS[language].get(severity, severity)

        parts = [
            '[%s] %s' % (label, title),
            '',
            message,
        ]

        if suggestion:
            suggestion = str(suggestion).strip()

            if suggestion:
                parts += [
                    '',
                    texts['suggestion'],
                    self._indent(suggestion),
                ]

        return '\n'.join(parts)

    ######################################################################
    # Helpers
    ######################################################################

    def _create_client(self) -> BaseChatClient:
        """Create the chat client from the tool settings."""
        settings = self.settings
        provider = get_provider(settings.get('provider'))

        return create_chat_client(
            provider=provider,
            api_key=resolve_api_key(provider, settings.get('api_key')),
            base_url=resolve_base_url(provider, settings.get('base_url')),
            model=resolve_model(provider, settings.get('model')),
            timeout=float(self._get_int('request_timeout',
                                       int(DEFAULT_TIMEOUT))),
            logger=self.logger)

    def _get_repository_name(self, kwargs: dict[str, Any]) -> str | None:
        """Return the repository name from ``kwargs``, or ``None``."""
        repository = kwargs.get('repository')

        if repository is not None:
            name = getattr(repository, 'name', None)

            if name:
                return str(name)

        return None

    def _get_int(self, name: str, default: int) -> int:
        """Return an integer tool setting, falling back to ``default``."""
        try:
            return int(self.settings.get(name) or default)
        except (TypeError, ValueError):
            return default

    def _get_output_language(self) -> str:
        """Return the configured output language, defaulting to Chinese."""
        return resolve_output_language(self.settings.get('language'))

    def _get_min_severity(self) -> str:
        """Return the configured minimum severity."""
        value = str(self.settings.get('min_severity') or 'low').lower()

        if value not in SEVERITY_RANK:
            return 'low'

        return value

    def _coerce_line(self, value: Any) -> int | None:
        """Convert a model-reported line number to an int, or ``None``."""
        if isinstance(value, bool):
            return None

        if isinstance(value, int):
            return value

        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _normalize_path(self, path: str) -> str:
        """Normalize a file path for comparison."""
        path = path.strip().replace('\\', '/')

        while path.startswith('./'):
            path = path[2:]

        for prefix in ('a/', 'b/'):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

        return path

    def _indent(self, text: str, prefix: str = '    ') -> str:
        """Indent every non-empty line of a block of text."""
        return '\n'.join(
            prefix + line if line.strip() else line
            for line in text.splitlines())
