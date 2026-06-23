"""Structured session state stored on Conversation (database-backed exam/quiz progress)."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Agent, Conversation

logger = logging.getLogger(__name__)

MCQ_ANSWER_RE = re.compile(r'^([A-Da-d])[\).\]:]?\s*$')
NAME_RE = re.compile(
    r"(?:my name is|i am|i'm|name is|call me)\s+([A-Za-z][A-Za-z\s'.-]{1,80})",
    re.IGNORECASE,
)
LEVEL_RE = re.compile(r'^(?:level\s*)?([123])\s*$', re.IGNORECASE)

EXAM_INSTRUCTION_SIGNALS = (
    '30 question',
    '30 multiple',
    'certification examiner',
    'mock test',
    'question 1/30',
    'current_question_number',
    'passing score',
    '/30',
    'questions): 30',
    'total questions: 30',
    'scrum master',
    'scrum exam',
)


def structured_session_state_enabled(agent: 'Agent') -> bool:
    """Whether to use DB session_state instead of long chat history for context."""
    cfg = agent.configuration or {}
    if cfg.get('use_structured_session_state') is True:
        return True
    mode = (cfg.get('session_state_mode') or '').lower()
    if mode in ('exam', 'quiz', 'assessment'):
        return True
    text = ' '.join(
        str(cfg.get(key) or '')
        for key in ('instruction', 'system_prompt', 'introduction')
    ).lower()
    return any(signal in text for signal in EXAM_INSTRUCTION_SIGNALS)


def default_exam_state(total_questions: int = 30) -> Dict[str, Any]:
    return {
        'mode': 'exam',
        'exam_started': False,
        'exam_completed': False,
        'user_name': '',
        'level': None,
        'current_question': 0,
        'total_questions': total_questions,
        'score': None,
        'answers': {},
        'awaiting_answer_for': 0,
    }


def total_questions_for_agent(agent: 'Agent') -> int:
    cfg = agent.configuration or {}
    try:
        return max(1, int(cfg.get('exam_total_questions', 30)))
    except (TypeError, ValueError):
        return 30


def get_session_state(conversation: 'Conversation') -> Dict[str, Any]:
    raw = conversation.session_state
    if isinstance(raw, dict) and raw.get('mode') == 'exam':
        return raw
    return default_exam_state(total_questions_for_agent(conversation.agent))


def save_session_state(conversation: 'Conversation', state: Dict[str, Any]) -> None:
    conversation.session_state = state
    conversation.save(update_fields=['session_state', 'updated_at'])


def parse_mcq_answer(message: str) -> Optional[str]:
    text = (message or '').strip()
    if not text:
        return None
    m = MCQ_ANSWER_RE.match(text)
    if m:
        return m.group(1).upper()
    if len(text) == 1 and text.upper() in 'ABCD':
        return text.upper()
    return None


def apply_user_message_to_session_state(
    agent: 'Agent',
    conversation: 'Conversation',
    user_message: str,
) -> Dict[str, Any]:
    """Update conversation.session_state from the latest user message. Returns new state."""
    if not structured_session_state_enabled(agent):
        return get_session_state(conversation)

    state = dict(get_session_state(conversation))
    total = state.get('total_questions') or total_questions_for_agent(agent)
    state['total_questions'] = total

    msg = (user_message or '').strip()
    if not msg:
        save_session_state(conversation, state)
        return state

    msg_upper = msg.upper()
    low = msg.lower()

    name_match = NAME_RE.search(msg)
    if name_match:
        state['user_name'] = name_match.group(1).strip().title()

    level_match = LEVEL_RE.match(low.strip())
    if level_match:
        state['level'] = int(level_match.group(1))
    elif low in ('level 1', 'level 2', 'level 3', 'level1', 'level2', 'level3'):
        state['level'] = int(low.replace('level', '').strip() or low[-1])

    if msg_upper in ('START', 'START EXAM', 'BEGIN', 'BEGIN EXAM'):
        if not state.get('exam_completed'):
            state['exam_started'] = True
            state['current_question'] = 1
            state['awaiting_answer_for'] = 1
        save_session_state(conversation, state)
        logger.info(
            'Session state: exam started for conversation %s',
            conversation.conversation_id,
        )
        return state

    letter = parse_mcq_answer(msg)
    if letter and state.get('exam_started') and not state.get('exam_completed'):
        q_num = state.get('awaiting_answer_for') or 0
        if q_num >= 1:
            answers = dict(state.get('answers') or {})
            answers[str(q_num)] = letter
            state['answers'] = answers

            if q_num >= total:
                state['exam_completed'] = True
                state['awaiting_answer_for'] = 0
                state['current_question'] = total + 1
                logger.info(
                    'Session state: exam completed (%s answers) conversation %s',
                    len(answers),
                    conversation.conversation_id,
                )
            else:
                next_q = q_num + 1
                state['awaiting_answer_for'] = next_q
                state['current_question'] = next_q

            save_session_state(conversation, state)
            return state

    save_session_state(conversation, state)
    return state


def trim_conversation_history(history: str, max_lines: int = 8) -> str:
    """Keep only the most recent lines when structured session state carries progress."""
    if not history or max_lines <= 0:
        return history
    lines = [ln for ln in history.split('\n') if ln.strip()]
    if len(lines) <= max_lines:
        return history
    trimmed = lines[-max_lines:]
    return '... [older messages omitted — use SESSION STATE below] ...\n' + '\n'.join(trimmed)


def build_session_state_prompt_block(conversation: 'Conversation') -> str:
    """Compact authoritative block injected into every LLM call."""
    from .exam_question_bank import format_question_display, load_question_bank, question_by_number

    state = get_session_state(conversation)
    if state.get('mode') != 'exam':
        return ''

    agent = conversation.agent
    level = state.get('level') or 1
    bank = load_question_bank(agent, level)

    total = int(state.get('total_questions') or 30)
    answers = state.get('answers') or {}
    answered = len(answers)
    lines = [
        '=== SESSION STATE (authoritative — trust this over summarized or truncated chat history) ===',
    ]

    if state.get('user_name'):
        lines.append(f"User name: {state['user_name']}")
    if state.get('level') is not None:
        lines.append(f"Exam level: {state['level']}")

    if state.get('exam_completed'):
        lines.append(f"Exam status: COMPLETED ({answered}/{total} answers recorded)")
    elif state.get('exam_started'):
        awaiting = state.get('awaiting_answer_for') or state.get('current_question') or 1
        lines.append(f"Exam status: IN PROGRESS — present or grade question {awaiting}/{total}")
        lines.append(f"Answers recorded: {answered}/{total}")
    else:
        lines.append(
            f"Exam status: NOT STARTED — collect level/name if needed, then user types START "
            f"({total} questions total)"
        )

    if answers:
        summary = ', '.join(f"{k}:{v}" for k, v in sorted(answers.items(), key=lambda x: int(x[0])))
        if len(summary) > 1200:
            summary = summary[:1200] + '...'
        lines.append(f"Answer key (user choices): {summary}")

    if bank and state.get('exam_started') and not state.get('exam_completed'):
        next_num = state.get('awaiting_answer_for') or state.get('current_question') or 1
        next_q = question_by_number(bank, next_num)
        if next_q:
            lines.append('')
            lines.append('NEXT QUESTION TO PRESENT (use this exact text; do not invent a new question):')
            lines.append(format_question_display(next_q, next_num, total))

    lines.extend([
        'Rules: Respond to the user only — no internal planning, no "We need to", no chain-of-thought.',
        'Do NOT restart the exam or re-ask question 1 unless the user explicitly asks to reset.',
        'If NEXT QUESTION TO PRESENT is shown above, output that question verbatim after brief feedback.',
        '=== END SESSION STATE ===',
    ])
    return '\n'.join(lines)
