from django.test import TestCase

# Import all unit test modules
from .tests_tools import (
    WriteFileToolTestCase,
    ToolExecutorFileRegistrationTestCase,
    FileURLGenerationTestCase,
    ToolManagerTestCase
)
from .tests_views_chat import (
    ChatHomeViewTestCase,
    SendChatMessageViewTestCase,
    GetConversationViewTestCase,
    UploadChatFileViewTestCase,
    GetConversationsListViewTestCase,
    UserHasAgentAccessTestCase
)
from .tests_file_serving import ServeMediaFileViewTestCase
from .tests_models import (
    ConversationFileModelTestCase,
    ConversationModelTestCase,
    ConversationMessageModelTestCase
)
from .tests_tools_comprehensive import (
    ReadFileToolTestCase,
    CodeExecutorToolTestCase,
    WebSearchToolTestCase,
    URLResolverToolTestCase,
    TextToImageToolTestCase,
    OCRToolTestCase,
    SummarizationToolTestCase,
    QuestionAnsweringToolTestCase,
    TranslationToolTestCase,
    ToolManagerComprehensiveTestCase
)
from .tests_agent_model import (
    AgentModelMethodsTestCase,
    AgentStringRepresentationTestCase
)
from .tests_agent_sharing import (
    AgentShareModelTestCase,
    AgentPublicShareModelTestCase
)
from .tests_utils import UtilsTestCase
from .tests_authentication import (
    AgentAPIKeyAuthenticationTestCase,
    UserAPIKeyAuthenticationTestCase,
    SessionOrAgentAPIKeyAuthenticationTestCase
)
from .tests_permissions import (
    IsAgentOwnerTestCase,
    IsAgentOwnerOrReadOnlyTestCase,
    HasAgentAPIKeyTestCase,
    IsPublishedAgentTestCase,
    SessionOrAgentAPIKeyPermissionTestCase
)

# Create your tests here.
