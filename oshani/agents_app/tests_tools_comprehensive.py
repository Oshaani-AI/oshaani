"""
Comprehensive unit tests for all tools.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from unittest.mock import Mock, patch
import json

from .models import Agent
from .tools import (
    WriteFileTool, ReadFileTool, WebSearchTool, URLResolverTool,
    CodeExecutorTool, TextToImageTool, OCRTool, SummarizationTool,
    QuestionAnsweringTool, TranslationTool, ToolManager
)


class ReadFileToolTestCase(TestCase):
    """Test cases for ReadFileTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = ReadFileTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'read_file')
        self.assertIn('description', schema)
        self.assertIn('parameters', schema)
    
    def test_execute_missing_file_path(self):
        """Test that execute returns error when file_path is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    @patch('agents_app.tools.OCRTool')
    def test_execute_delegates_to_ocr_tool(self, mock_ocr_tool_class):
        """Test that ReadFileTool delegates to OCRTool."""
        mock_ocr_tool = Mock()
        mock_ocr_tool.execute.return_value = {
            'success': True,
            'text': 'File content',
            'file_type': 'text'
        }
        mock_ocr_tool_class.return_value = mock_ocr_tool
        
        result = self.tool.execute({'file_path': 'test.txt'})
        
        mock_ocr_tool.execute.assert_called_once_with({'file_path': 'test.txt'})
        self.assertIn('content', result)


class CodeExecutorToolTestCase(TestCase):
    """Test cases for CodeExecutorTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = CodeExecutorTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'code_executor')
        self.assertIn('description', schema)
    
    def test_execute_missing_code(self):
        """Test that execute returns error when code is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    def test_validate_code_security_blocks_dangerous_imports(self):
        """Test that dangerous imports are blocked."""
        dangerous_code = "import os\nprint('dangerous')"
        is_safe, error_msg = self.tool._validate_code_security(dangerous_code)
        self.assertFalse(is_safe)
        self.assertIn('os module', error_msg)
    
    def test_validate_code_security_allows_safe_code(self):
        """Test that safe code is allowed."""
        safe_code = "result = 2 + 2\nprint(result)"
        is_safe, error_msg = self.tool._validate_code_security(safe_code)
        self.assertTrue(is_safe)
        self.assertEqual(error_msg, "")
    
    def test_validate_code_security_blocks_code_too_long(self):
        """Test that code exceeding length limit is blocked."""
        long_code = "x = 1\n" * 2000  # Very long code
        is_safe, error_msg = self.tool._validate_code_security(long_code)
        self.assertFalse(is_safe)
        self.assertIn('maximum length', error_msg)
    
    @patch('agents_app.tools.subprocess.run')
    def test_execute_safe_code(self, mock_subprocess):
        """Test executing safe code."""
        mock_result = Mock()
        mock_result.stdout = "4"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_subprocess.return_value = mock_result
        
        result = self.tool.execute({
            'code': 'result = 2 + 2\nprint(result)',
            'language': 'python'
        })
        
        self.assertTrue(result.get('success'))
        self.assertIn('stdout', result)


class WebSearchToolTestCase(TestCase):
    """Test cases for WebSearchTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = WebSearchTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'web_search')
        self.assertIn('description', schema)
    
    def test_execute_missing_query(self):
        """Test that execute returns error when query is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    @patch('agents_app.tools.requests.get')
    def test_execute_success(self, mock_get):
        """Test successful web search."""
        # Force the Google Custom Search path (otherwise it falls back to a live
        # DuckDuckGo query) so the mocked requests.get is exercised deterministically.
        self.tool.api_key = 'test-api-key'
        self.tool.search_engine_id = 'test-engine-id'
        
        mock_response = Mock()
        mock_response.json.return_value = {
            'items': [
                {
                    'title': 'Test Result',
                    'snippet': 'Test snippet',
                    'link': 'https://example.com'
                }
            ],
            'searchInformation': {'totalResults': '1'}
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        result = self.tool.execute({'query': 'test query'})
        
        self.assertIn('results', result)
        self.assertEqual(len(result['results']), 1)


class URLResolverToolTestCase(TestCase):
    """Test cases for URLResolverTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = URLResolverTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'url_resolver')
        self.assertIn('description', schema)
    
    def test_execute_missing_url(self):
        """Test that execute returns error when URL is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    def test_execute_blocks_path_traversal(self):
        """Test that path traversal attacks are blocked."""
        result = self.tool.execute({
            'url': 'file:///etc/passwd'
        })
        
        self.assertIn('error', result)
        self.assertIn('Security violation', result['error'])
    
    @patch('agents_app.tools.requests.get')
    def test_execute_http_url(self, mock_get):
        """Test resolving HTTP URL."""
        mock_response = Mock()
        mock_response.text = 'Web page content'
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'text/html'}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        result = self.tool.execute({
            'url': 'https://example.com'
        })
        
        self.assertIn('content', result)
        self.assertEqual(result['status_code'], 200)


class TextToImageToolTestCase(TestCase):
    """Test cases for TextToImageTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = TextToImageTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'text_to_image')
        self.assertIn('description', schema)
    
    def test_execute_missing_text_prompt(self):
        """Test that execute returns error when text_prompt is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    @patch('agents_app.tools.default_storage')
    @patch('agents_app.aws_utils.create_boto3_client')
    def test_execute_success(self, mock_create_client, mock_storage):
        """Test successful image generation."""
        # Mock AWS Bedrock client
        mock_bedrock_runtime = Mock()
        mock_bedrock_client = Mock()
        mock_create_client.side_effect = [mock_bedrock_runtime, mock_bedrock_client]
        
        # Mock model listing
        mock_bedrock_client.list_foundation_models.return_value = {
            'modelSummaries': [
                {'modelId': 'amazon.titan-image-generator-v1'}
            ]
        }
        
        # Mock image generation response
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            'images': [{'image': 'base64encodedimage'}]
        }).encode()
        mock_bedrock_runtime.invoke_model.return_value = {'body': mock_response}
        
        # Mock storage
        mock_storage.save.return_value = 'generated_images/test.png'
        mock_storage.url.return_value = '/media/generated_images/test.png'
        
        result = self.tool.execute({
            'text_prompt': 'A beautiful sunset',
            'aspect_ratio': '1:1'
        })
        
        # Should return image_url (even if AWS is not configured, it will return error with helpful message)
        self.assertIn('image_url', result or {})


class OCRToolTestCase(TestCase):
    """Test cases for OCRTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = OCRTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'ocr')
        self.assertIn('description', schema)
    
    def test_execute_missing_file_path(self):
        """Test that execute returns error when file_path is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    @patch('agents_app.tools.default_storage')
    def test_resolve_file_path_from_conversation_file(self, mock_storage):
        """Test that file path resolution works for ConversationFile."""
        from .models import ConversationFile
        
        # Create a mock agent
        user = User.objects.create_user(username='test', email='test@test.com', password='pass')
        agent = Agent.objects.create(name='Test', user=user, status='published', configuration={})
        
        # Create ConversationFile
        conv_file = ConversationFile.objects.create(
            agent=agent,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id='test-file-id',
            download_url=''
        )
        conv_file.file_path = 'conversation_files/1/test-file-id.txt'
        conv_file.save()
        
        # Mock storage
        mock_storage.exists.return_value = True
        
        # Test resolution
        resolved_path = self.tool._resolve_file_path('test-file-id')
        self.assertIn('conversation_files', resolved_path)


class SummarizationToolTestCase(TestCase):
    """Test cases for SummarizationTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = SummarizationTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'summarization')
        self.assertIn('description', schema)
    
    def test_execute_missing_file_path_and_content(self):
        """Test that execute returns error when both file_path and text_content are missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    def test_execute_with_text_content(self):
        """Test summarization with direct text content."""
        long_text = "This is a very long text. " * 50
        
        # Mock AWS Bedrock client
        with patch('agents_app.aws_integration.get_bedrock_client') as mock_get_client:
            mock_client = Mock()
            mock_client.invoke_agent.return_value = {
                'response': 'This is a summary of the text.'
            }
            mock_get_client.return_value = mock_client
            
            result = self.tool.execute({
                'text_content': long_text,
                'max_length': 100
            })
            
            # Should return summary or fallback
            self.assertIn('summary', result or {})


class QuestionAnsweringToolTestCase(TestCase):
    """Test cases for QuestionAnsweringTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = QuestionAnsweringTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'question_answering')
        self.assertIn('description', schema)
    
    def test_execute_missing_question(self):
        """Test that execute returns error when question is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    def test_execute_missing_file_path_and_content(self):
        """Test that execute returns error when both file_path and text_content are missing."""
        result = self.tool.execute({'question': 'What is this?'})
        self.assertIn('error', result)


class TranslationToolTestCase(TestCase):
    """Test cases for TranslationTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = TranslationTool()
    
    def test_tool_schema(self):
        """Test that tool schema is correctly defined."""
        schema = self.tool.get_schema()
        self.assertEqual(schema['name'], 'translation')
        self.assertIn('description', schema)
    
    def test_execute_missing_target_language(self):
        """Test that execute returns error when target_language is missing."""
        result = self.tool.execute({})
        self.assertIn('error', result)
    
    def test_execute_missing_file_path_and_content(self):
        """Test that execute returns error when both file_path and text_content are missing."""
        result = self.tool.execute({'target_language': 'es'})
        self.assertIn('error', result)


class ToolManagerComprehensiveTestCase(TestCase):
    """Comprehensive test cases for ToolManager."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool_manager = ToolManager()
    
    def test_all_default_tools_registered(self):
        """Test that all default tools are registered."""
        expected_tools = [
            'read_file', 'write_file', 'web_search', 'url_resolver',
            'code_executor', 'transcription', 'text_to_image', 'ocr',
            'summarization', 'question_answering', 'translation'
        ]
        
        all_tools = self.tool_manager.get_all_tools()
        tool_names = list(all_tools.keys())
        
        for tool_name in expected_tools:
            self.assertIn(tool_name, tool_names, f"Tool {tool_name} not registered")
    
    def test_get_tool_returns_correct_tool(self):
        """Test that get_tool returns the correct tool instance."""
        write_file_tool = self.tool_manager.get_tool('write_file')
        self.assertIsNotNone(write_file_tool)
        self.assertIsInstance(write_file_tool, WriteFileTool)
        
        read_file_tool = self.tool_manager.get_tool('read_file')
        self.assertIsNotNone(read_file_tool)
        self.assertIsInstance(read_file_tool, ReadFileTool)
    
    def test_get_tool_returns_none_for_invalid_tool(self):
        """Test that get_tool returns None for non-existent tool."""
        tool = self.tool_manager.get_tool('non_existent_tool')
        self.assertIsNone(tool)
    
    def test_get_tools_schema_includes_all_tools(self):
        """Test that get_tools_schema includes all registered tools."""
        schema = self.tool_manager.get_tools_schema()
        tool_names = [tool['name'] for tool in schema]
        
        # Should have at least the default tools
        self.assertGreaterEqual(len(tool_names), 11)
        self.assertIn('write_file', tool_names)
        self.assertIn('read_file', tool_names)

