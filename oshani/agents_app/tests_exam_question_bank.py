"""Tests for exam question bank parsing and deterministic replies."""
import uuid

from django.contrib.auth.models import User
from django.test import TestCase

from .conversation_session_state import apply_user_message_to_session_state
from .exam_question_bank import (
    build_exam_deterministic_reply,
    parse_questions_from_markdown,
    load_question_bank,
)
from .models import Agent, Conversation, TrainingData

SAMPLE_BANK = """
### LEVEL 1: AI FUNDAMENTALS (30 QUESTIONS)

**Question 1**
What does AI stand for?
A) Automated Intelligence
B) Artificial Intelligence
C) Advanced Integration
D) Analytical Information
**Correct Answer: B**

**Question 2**
Which is supervised learning?
A) Clustering
B) Predicting house prices
C) Trial and error
D) Grouping news
**Correct Answer: B**

**Question 3**
Classification vs regression?
A) Categories vs continuous values
B) Faster vs slower
C) Text only
D) More data
**Correct Answer: A**

**Question 4**
What is a feature?
A) A bug
B) An input variable
C) Final output
D) Neural network
**Correct Answer: B**
"""


class ExamQuestionBankTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bankuser', password='pass')
        self.agent = Agent.objects.create(
            name='Exam Agent',
            user=self.user,
            status='published',
            configuration={'use_structured_session_state': True, 'exam_total_questions': 4},
        )
        TrainingData.objects.create(
            agent=self.agent,
            data_type='text',
            content={'text': SAMPLE_BANK},
        )
        self.conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
        )

    def test_parse_markdown_questions(self):
        qs = parse_questions_from_markdown(SAMPLE_BANK, level=1)
        self.assertEqual(len(qs), 4)
        self.assertEqual(qs[0]['correct'], 'B')
        self.assertIn('Artificial Intelligence', qs[0]['options']['B'])

    def test_load_from_training_data(self):
        bank = load_question_bank(self.agent, 1)
        self.assertEqual(len(bank), 4)

    def test_deterministic_flow_question_4_after_answer_c(self):
        apply_user_message_to_session_state(self.agent, self.conversation, 'START')
        apply_user_message_to_session_state(self.agent, self.conversation, 'B')
        apply_user_message_to_session_state(self.agent, self.conversation, 'B')
        apply_user_message_to_session_state(self.agent, self.conversation, 'A')
        self.conversation.refresh_from_db()

        reply = build_exam_deterministic_reply(self.agent, self.conversation, 'C')
        self.assertIsNotNone(reply)
        self.assertIn('Question 4/4', reply)
        self.assertIn('feature', reply.lower())
        self.assertNotIn('We need to', reply)

    def test_start_presents_question_1(self):
        apply_user_message_to_session_state(self.agent, self.conversation, 'START')
        reply = build_exam_deterministic_reply(self.agent, self.conversation, 'START')
        self.assertIn('Question 1/4', reply)
        self.assertIn('Artificial Intelligence', reply)
