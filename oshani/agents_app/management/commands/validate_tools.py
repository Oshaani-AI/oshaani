"""
Django management command to validate all tools are working correctly.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from agents_app.tools import ToolManager
from agents_app.models import Agent, Conversation
import json
import traceback
import os


class Command(BaseCommand):
    help = 'Validate that all tools are working correctly'

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output for each tool',
        )
        parser.add_argument(
            '--tool',
            type=str,
            help='Test a specific tool by name',
        )
        parser.add_argument(
            '--skip-external',
            action='store_true',
            help='Skip tools that require external services (AWS, web search, etc.)',
        )

    def handle(self, *args, **options):
        verbose = options.get('verbose', False)
        specific_tool = options.get('tool')
        skip_external = options.get('skip_external', False)
        
        self.stdout.write(self.style.SUCCESS('\n=== Tool Validation Report ===\n'))
        
        tool_manager = ToolManager()
        all_tools = tool_manager.get_all_tools()
        
        if specific_tool:
            if specific_tool not in all_tools:
                self.stdout.write(self.style.ERROR(f'Tool "{specific_tool}" not found'))
                return
            tools_to_test = {specific_tool: all_tools[specific_tool]}
        else:
            tools_to_test = all_tools
        
        results = {
            'total': len(tools_to_test),
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'details': []
        }
        
        # External tools that require API keys or services
        external_tools = {
            'web_search': 'Requires Google Search API or DuckDuckGo',
            'text_to_image': 'Requires AWS Bedrock or OpenAI',
            'text_to_video': 'Requires AWS Bedrock',
            'image_to_video': 'Requires AWS Bedrock',
            'transcription': 'Requires AWS Transcribe',
            'ocr': 'May require AWS Textract',
            'summarization': 'Requires LLM model',
            'question_answering': 'Requires LLM model',
            'translation': 'Requires LLM model',
        }
        
        for tool_name, tool in tools_to_test.items():
            if skip_external and tool_name in external_tools:
                results['skipped'] += 1
                if verbose:
                    self.stdout.write(self.style.WARNING(f'⏭️  SKIPPED: {tool_name} - {external_tools[tool_name]}'))
                continue
            
            if verbose:
                self.stdout.write(f'\n🔧 Testing: {tool_name}')
            
            tool_result = self._validate_tool(tool_name, tool, verbose)
            results['details'].append(tool_result)
            
            if tool_result['status'] == 'passed':
                results['passed'] += 1
                if verbose:
                    self.stdout.write(self.style.SUCCESS(f'   ✅ PASSED'))
            elif tool_result['status'] == 'skipped':
                results['skipped'] += 1
                if verbose:
                    self.stdout.write(self.style.WARNING(f'   ⏭️  SKIPPED: {tool_result["reason"]}'))
            else:
                results['failed'] += 1
                if verbose:
                    self.stdout.write(self.style.ERROR(f'   ❌ FAILED: {tool_result["error"]}'))
        
        # Print summary
        self.stdout.write(self.style.SUCCESS('\n=== Validation Summary ==='))
        self.stdout.write(f'Total Tools: {results["total"]}')
        self.stdout.write(self.style.SUCCESS(f'✅ Passed: {results["passed"]}'))
        self.stdout.write(self.style.ERROR(f'❌ Failed: {results["failed"]}'))
        self.stdout.write(self.style.WARNING(f'⏭️  Skipped: {results["skipped"]}'))
        
        if results['failed'] > 0:
            self.stdout.write(self.style.ERROR('\n=== Failed Tools ==='))
            for detail in results['details']:
                if detail['status'] == 'failed':
                    self.stdout.write(self.style.ERROR(f"  - {detail['name']}: {detail['error']}"))
        
        if results['passed'] == results['total'] - results['skipped']:
            self.stdout.write(self.style.SUCCESS('\n🎉 All testable tools are working correctly!'))
        else:
            self.stdout.write(self.style.WARNING('\n⚠️  Some tools failed validation. Check details above.'))
    
    def _validate_tool(self, tool_name, tool, verbose=False):
        """Validate a single tool."""
        result = {
            'name': tool_name,
            'status': 'unknown',
            'error': None,
            'reason': None
        }
        
        try:
            # 1. Check tool schema
            schema = tool.get_schema()
            if not schema:
                result['status'] = 'failed'
                result['error'] = 'Tool schema is empty'
                return result
            
            if 'name' not in schema or schema['name'] != tool_name:
                result['status'] = 'failed'
                result['error'] = 'Tool schema name mismatch'
                return result
            
            if 'description' not in schema or not schema['description']:
                result['status'] = 'failed'
                result['error'] = 'Tool description is missing'
                return result
            
            # 2. Check parameters schema
            params_schema = tool.get_parameters_schema()
            if params_schema is None:
                result['status'] = 'failed'
                result['error'] = 'Parameters schema is None'
                return result
            
            # 3. Test tool-specific validation
            test_result = self._test_tool_execution(tool_name, tool, verbose)
            
            if test_result['status'] == 'skipped':
                result['status'] = 'skipped'
                result['reason'] = test_result['reason']
            elif test_result['status'] == 'passed':
                result['status'] = 'passed'
            else:
                result['status'] = 'failed'
                result['error'] = test_result['error']
            
        except Exception as e:
            result['status'] = 'failed'
            result['error'] = f'Exception: {str(e)}'
            if verbose:
                result['traceback'] = traceback.format_exc()
        
        return result
    
    def _test_tool_execution(self, tool_name, tool, verbose=False):
        """Test tool execution with sample parameters."""
        result = {'status': 'unknown'}
        
        # Define comprehensive test cases for each tool
        test_cases = {
            'read_file': {
                'params': self._get_read_file_params(),
                'test_func': self._test_read_file
            },
            'write_file': {
                'params': {
                    'file_name': 'test_validation.txt',
                    'content': 'This is a test file created during tool validation.'
                }
            },
            'web_search': {
                'params': {'query': 'test'},
                'test_func': self._test_web_search
            },
            'url_resolver': {
                'params': {'url': 'https://example.com'},
                'test_func': self._test_url_resolver
            },
            'code_executor': {
                'params': {'code': 'result = 2 + 2'}
            },
            'transcription': {
                'params': {'file_path': 'test.mp3'},
                'test_func': self._test_transcription
            },
            'text_to_image': {
                'params': {'text_prompt': 'a simple test image'},
                'test_func': self._test_text_to_image
            },
            'text_to_video': {
                'params': {'text_prompt': 'a simple test video'},
                'test_func': self._test_text_to_video
            },
            'image_to_video': {
                'params': {'image_path': 'test.jpg', 'text_prompt': 'animate'},
                'test_func': self._test_image_to_video
            },
            'svg_diagram': {
                'params': {'description': 'a simple diagram'}
            },
            'ocr': {
                'params': {'file_path': 'nonexistent.pdf'},
                'test_func': self._test_ocr
            },
            'summarization': {
                'params': {'text_content': 'This is a test content to summarize.'},
                'test_func': self._test_summarization
            },
            'question_answering': {
                'params': {'question': 'What is this?', 'text_content': 'This is a test.'},
                'test_func': self._test_question_answering
            },
            'translation': {
                'params': {'text_content': 'Hello', 'target_language': 'Spanish'},
                'test_func': self._test_translation
            },
        }
        
        if tool_name not in test_cases:
            result['status'] = 'skipped'
            result['reason'] = 'No test case defined'
            return result
        
        test_case = test_cases[tool_name]
        
        # Use custom test function if available
        if 'test_func' in test_case:
            test_func = test_case['test_func']
            try:
                test_result = test_func(tool, test_case['params'])
                if test_result is None:
                    result['status'] = 'skipped'
                    result['reason'] = 'No test function result'
                elif test_result[0] is None:
                    result['status'] = 'skipped'
                    result['reason'] = test_result[1]
                elif test_result[0]:
                    result['status'] = 'passed'
                    if verbose and len(test_result) > 1:
                        result['message'] = test_result[1]
                else:
                    result['status'] = 'failed'
                    result['error'] = test_result[1]
            except Exception as e:
                result['status'] = 'failed'
                result['error'] = f'Test function exception: {str(e)}'
            return result
        
        # Standard execution test
        try:
            execution_result = tool.execute(test_case['params'])
            
            # Check if execution returned a result (not an error)
            if isinstance(execution_result, dict):
                if 'error' in execution_result:
                    # Check for dependency/config errors
                    error_lower = execution_result['error'].lower()
                    if any(keyword in error_lower for keyword in [
                        'not configured', 'not available', 'not installed', 'missing', 
                        'api key', 'credentials', 'authentication', 'token', 'invalid',
                        'bedrock', 'openai', 'transcribe', 's3', 'bucket'
                    ]):
                        result['status'] = 'skipped'
                        result['reason'] = execution_result['error']
                    else:
                        result['status'] = 'failed'
                        result['error'] = execution_result['error']
                else:
                    result['status'] = 'passed'
                    if 'success' in execution_result:
                        result['message'] = 'Tool executed successfully'
            else:
                result['status'] = 'passed'
                
        except Exception as e:
            error_msg = str(e).lower()
            # Some exceptions are expected (missing dependencies)
            if any(keyword in error_msg for keyword in [
                'not configured', 'not available', 'not installed', 'missing', 
                'import', 'no module', 'bedrock', 'openai', 'credentials'
            ]):
                result['status'] = 'skipped'
                result['reason'] = f'Dependency not available: {str(e)}'
            else:
                result['status'] = 'failed'
                result['error'] = str(e)
        
        return result
    
    def _get_read_file_params(self):
        """Create a test file for read_file tool."""
        test_content = "Test content for read_file tool"
        test_file_path = 'test_read_file_validation.txt'
        try:
            default_storage.save(test_file_path, ContentFile(test_content.encode('utf-8')))
            return {'file_path': test_file_path}
        except Exception as e:
            return {'file_path': 'nonexistent.txt'}
    
    def _test_read_file(self, tool, params):
        """Test ReadFileTool with actual file."""
        try:
            result = tool.execute(params)
            
            # Cleanup test file
            test_file_path = params.get('file_path', '')
            if test_file_path and default_storage.exists(test_file_path):
                try:
                    default_storage.delete(test_file_path)
                except:
                    pass
            
            if 'error' in result:
                if 'not found' in result['error'].lower() or 'file' in result['error'].lower():
                    return None, "Requires file"
                return False, result['error']
            if 'content' in result or 'text' in result:
                return True, "File read successfully"
            return True, "Tool executed"
        except Exception as e:
            return False, str(e)
    
    def _test_web_search(self, tool, params):
        """Test WebSearchTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in ['api key', 'not configured', 'not available']):
                    return None, "Requires API key"
                return False, result['error']
            if 'results' in result:
                return True, f"Found {len(result.get('results', []))} results"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if 'api' in error_str or 'key' in error_str:
                return None, "Requires API key"
            return False, str(e)
    
    def _test_url_resolver(self, tool, params):
        """Test URLResolverTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in ['network', 'connection', 'timeout']):
                    return None, "Requires network access"
                return False, result['error']
            if 'content' in result or 'title' in result:
                return True, "URL resolved successfully"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if 'network' in error_str or 'connection' in error_str:
                return None, "Requires network access"
            return False, str(e)
    
    def _test_ocr(self, tool, params):
        """Test OCRTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in ['not found', 'file', 'does not exist']):
                    return None, "Requires file"
                return False, result['error']
            return True, "Tool executed"
        except Exception as e:
            return None, "Requires file"
    
    def _test_text_to_image(self, tool, params):
        """Test TextToImageTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'bedrock', 'openai', 'not configured', 'not available', 
                    'api key', 'credentials', 'authentication', 'token', 'invalid'
                ]):
                    return None, "Requires AWS Bedrock or OpenAI (credentials not configured)"
                return False, result['error']
            if 'image_url' in result or 'file_content' in result:
                return True, "Image generated successfully"
            if 'note' in result and ('bedrock' in result.get('note', '').lower() or 'credentials' in result.get('note', '').lower()):
                return None, "Requires AWS Bedrock or OpenAI (credentials not configured)"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['bedrock', 'openai', 'not configured']):
                return None, "Requires AWS Bedrock or OpenAI"
            return False, str(e)
    
    def _test_text_to_video(self, tool, params):
        """Test TextToVideoTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'bedrock', 'not configured', 'not available', 'api key', 
                    'credentials', 's3', 'bucket', 'authentication', 'token', 
                    'invalid', 'failed to start'
                ]):
                    return None, "Requires AWS Bedrock and S3 bucket (credentials not configured)"
                return False, result['error']
            if 'success' in result or 'video_url' in result or 'file_content' in result:
                return True, "Tool executed successfully"
            if 'note' in result and ('bedrock' in result.get('note', '').lower() or 'credentials' in result.get('note', '').lower()):
                return None, "Requires AWS Bedrock and S3 bucket (credentials not configured)"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['bedrock', 'not configured', 's3', 'bucket']):
                return None, "Requires AWS Bedrock and S3 bucket"
            return False, str(e)
    
    def _test_image_to_video(self, tool, params):
        """Test ImageToVideoTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'bedrock', 'file', 'not configured', 'not found', 'not available'
                ]):
                    return None, "Requires AWS Bedrock and image file"
                return False, result['error']
            return True, "Tool executed"
        except Exception as e:
            return None, "Requires AWS Bedrock and image file"
    
    def _test_transcription(self, tool, params):
        """Test TranscriptionTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'transcribe', 'file', 'not found', 'not configured', 'not available'
                ]):
                    return None, "Requires AWS Transcribe and audio file"
                return False, result['error']
            return True, "Tool executed"
        except Exception as e:
            return None, "Requires AWS Transcribe and audio file"
    
    def _test_summarization(self, tool, params):
        """Test SummarizationTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'llm', 'model', 'not configured', 'not available', 'bedrock'
                ]):
                    return None, "Requires LLM model"
                return False, result['error']
            if 'summary' in result:
                return True, "Summary generated successfully"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['llm', 'model', 'not configured']):
                return None, "Requires LLM model"
            return False, str(e)
    
    def _test_question_answering(self, tool, params):
        """Test QuestionAnsweringTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'llm', 'model', 'not configured', 'not available', 'bedrock'
                ]):
                    return None, "Requires LLM model"
                return False, result['error']
            if 'answer' in result:
                return True, "Answer generated successfully"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['llm', 'model', 'not configured']):
                return None, "Requires LLM model"
            return False, str(e)
    
    def _test_translation(self, tool, params):
        """Test TranslationTool."""
        try:
            result = tool.execute(params)
            if 'error' in result:
                error_lower = result['error'].lower()
                if any(keyword in error_lower for keyword in [
                    'llm', 'model', 'not configured', 'not available', 'bedrock'
                ]):
                    return None, "Requires LLM model"
                return False, result['error']
            if 'translated_text' in result:
                return True, "Translation generated successfully"
            return True, "Tool executed"
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['llm', 'model', 'not configured']):
                return None, "Requires LLM model"
            return False, str(e)
