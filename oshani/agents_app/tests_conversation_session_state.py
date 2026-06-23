"""Tests for database-backed conversation session state."""
import uuid

from django.contrib.auth.models import User
from django.test import TestCase

from .conversation_session_state import (
    apply_user_message_to_session_state,
    build_session_state_prompt_block,
    parse_mcq_answer,
    structured_session_state_enabled,
)
from .models import Agent, Conversation


class ConversationSessionStateTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='stateuser', password='pass')
        self.agent = Agent.objects.create(
            name='Exam Agent',
            user=self.user,
            status='published',
            configuration={
                'instruction': 'You are a certification examiner. 30 multiple-choice questions.',
                'use_structured_session_state': True,
            },
        )
        self.conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
        )

    def test_structured_session_enabled_by_config(self):
        self.assertTrue(structured_session_state_enabled(self.agent))

    def test_parse_mcq_answer(self):
        self.assertEqual(parse_mcq_answer('B'), 'B')
        self.assertEqual(parse_mcq_answer('c)'), 'C')
        self.assertIsNone(parse_mcq_answer('hello'))

    def test_exam_flow_stored_in_database(self):
        apply_user_message_to_session_state(self.agent, self.conversation, 'My name is Manoj')
        apply_user_message_to_session_state(self.agent, self.conversation, 'START')
        self.conversation.refresh_from_db()
        state = self.conversation.session_state
        self.assertTrue(state['exam_started'])
        self.assertEqual(state['awaiting_answer_for'], 1)
        self.assertEqual(state['user_name'], 'Manoj')

        apply_user_message_to_session_state(self.agent, self.conversation, 'B')
        self.conversation.refresh_from_db()
        state = self.conversation.session_state
        self.assertEqual(state['answers']['1'], 'B')
        self.assertEqual(state['awaiting_answer_for'], 2)

        block = build_session_state_prompt_block(self.conversation)
        self.assertIn('SESSION STATE', block)
        self.assertIn('1:B', block)
        self.assertIn('IN PROGRESS', block)

    def test_exam_completes_after_total_questions(self):
        self.agent.configuration['exam_total_questions'] = 2
        self.agent.save()
        apply_user_message_to_session_state(self.agent, self.conversation, 'START')
        apply_user_message_to_session_state(self.agent, self.conversation, 'A')
        apply_user_message_to_session_state(self.agent, self.conversation, 'C')
        self.conversation.refresh_from_db()
        state = self.conversation.session_state
        self.assertTrue(state['exam_completed'])
        self.assertEqual(state['answers'], {'1': 'A', '2': 'C'})
