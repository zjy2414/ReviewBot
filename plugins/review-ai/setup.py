#!/usr/bin/env python
"""Packaging for Review AI, a multi-provider AI review tool for Review Bot."""

from __future__ import annotations

import re

from pathlib import Path

from setuptools import find_packages, setup


here = Path(__file__).parent.resolve()

# Read the version without importing the package. Importing it would require
# reviewbot-worker to already be installed, which is not true inside an
# isolated build environment.
init_py = (here / 'review_ai' / '__init__.py').read_text('utf-8')
version = re.search(r"^__version__ = '([^']+)'",
                    init_py,
                    re.MULTILINE).group(1)

readme_path = here / 'README.md'
readme = readme_path.read_text('utf-8') if readme_path.exists() else ''


setup(
    name='review-ai',
    version=version,
    license='MIT',
    description=(
        'Multi-provider AI code review tool for Review Bot '
        '(DeepSeek, OpenAI, Qwen, Kimi, GLM, Claude, Ollama, '
        'and OpenAI-compatible gateways)'
    ),
    long_description=readme,
    long_description_content_type='text/markdown',
    packages=find_packages(exclude=['tests', 'tests.*']),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        'reviewbot.tools': [
            'reviewai = review_ai.tool:ReviewAITool',
        ],
    },
    install_requires=[
        # Only for the BaseTool API and the worker itself.
        'reviewbot-worker>=4.0',
        # LangChain chat models are the only transport.
        'langchain-core>=1.0',
        'langchain-openai>=1.0',
        'langchain-anthropic>=1.0',
    ],
    # LangChain requires Python 3.10+.
    python_requires=','.join([
        '>=3.10',
    ]),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Framework :: Review Board',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Software Development',
        'Topic :: Software Development :: Quality Assurance',
    ],
)
