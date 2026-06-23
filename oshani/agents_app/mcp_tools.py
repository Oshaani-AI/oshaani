"""MCP tool integration for the tool system."""
import logging
from typing import Dict, Any, List, Optional
from .tools import Tool
from .mcp_client import MCPClient, MCPClientManager
from .models import MCPServer

logger = logging.getLogger(__name__)


class MCPTool(Tool):
    """Tool wrapper for MCP server tools."""
    
    def __init__(self, mcp_client: MCPClient, tool_info: Dict[str, Any], server_name: str):
        """
        Initialize MCP tool.
        
        Args:
            mcp_client: The MCP client instance
            tool_info: Tool information from MCP server
            server_name: Name of the MCP server
        """
        tool_name = tool_info.get("name", "")
        description = tool_info.get("description", "")
        
        # Prefix tool name with server name to avoid conflicts
        prefixed_name = f"mcp_{server_name}_{tool_name}"
        
        super().__init__(
            name=prefixed_name,
            description=f"[MCP: {server_name}] {description}",
            instructions=tool_info.get("instructions", "")
        )
        self.mcp_client = mcp_client
        self.original_tool_name = tool_name
        self.server_name = server_name
        self.tool_info = tool_info
    
    def get_parameters_schema(self) -> List[Dict[str, Any]]:
        """Get parameters schema from MCP tool definition."""
        input_schema = self.tool_info.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        
        parameters = []
        for param_name, param_info in properties.items():
            param_type = param_info.get("type", "string")
            param_desc = param_info.get("description", "")
            
            parameters.append({
                "name": param_name,
                "description": param_desc,
                "type": param_type,
                "required": param_name in required
            })
        
        return parameters
    
    def execute(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the MCP tool."""
        try:
            result = self.mcp_client.call_tool(self.original_tool_name, parameters)
            
            # Handle MCP tool result format
            if "error" in result:
                return {
                    "error": result["error"],
                    "success": False
                }
            
            # Extract content from MCP result
            content = result.get("content", [])
            if content:
                # MCP returns content as list of objects with type and text
                text_parts = []
                for item in content:
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "image":
                        # Handle image content if needed
                        text_parts.append(f"[Image: {item.get('data', '')[:50]}...]")
                
                return {
                    "result": "\n".join(text_parts),
                    "raw": result,
                    "success": True
                }
            else:
                return {
                    "result": result,
                    "success": True
                }
        except Exception as e:
            logger.error(f"Error executing MCP tool {self.original_tool_name}: {str(e)}", exc_info=True)
            return {
                "error": f"Tool execution failed: {str(e)}",
                "success": False
            }


class MCPToolManager:
    """Manager for MCP tools integration."""
    
    def __init__(self, agent):
        """
        Initialize MCP tool manager for an agent.
        
        Args:
            agent: The Agent instance
        """
        self.agent = agent
        self.client_manager = MCPClientManager()
        self.mcp_tools = {}
        self._load_mcp_servers()
    
    def _load_mcp_servers(self):
        """Load and connect to MCP servers for this agent."""
        mcp_servers = MCPServer.objects.filter(agent=self.agent, is_active=True)
        
        for mcp_server in mcp_servers:
            try:
                client = MCPClient(
                    server_name=mcp_server.name,
                    transport_type=mcp_server.transport_type,
                    command=mcp_server.command if mcp_server.command else None,
                    args=mcp_server.args if mcp_server.args else [],
                    url=mcp_server.url if mcp_server.url else None,
                    headers=mcp_server.headers if mcp_server.headers else {}
                )
                
                if mcp_server.auto_connect:
                    if client.connect():
                        self.client_manager.add_client(mcp_server.name, client)
                        self._load_tools_from_server(mcp_server.name, client)
                    else:
                        logger.warning(f"Failed to connect to MCP server: {mcp_server.name}")
                else:
                    self.client_manager.add_client(mcp_server.name, client)
                    
            except Exception as e:
                logger.error(f"Error loading MCP server {mcp_server.name}: {str(e)}", exc_info=True)
    
    def _load_tools_from_server(self, server_name: str, client: MCPClient):
        """Load tools from an MCP server."""
        try:
            tools = client.list_tools()
            for tool_info in tools:
                tool_name = tool_info.get("name", "")
                if tool_name:
                    mcp_tool = MCPTool(client, tool_info, server_name)
                    self.mcp_tools[mcp_tool.name] = mcp_tool
                    logger.info(f"Loaded MCP tool: {mcp_tool.name} from server {server_name}")
        except Exception as e:
            logger.error(f"Error loading tools from MCP server {server_name}: {str(e)}", exc_info=True)
    
    def get_all_tools(self) -> Dict[str, MCPTool]:
        """Get all MCP tools."""
        return self.mcp_tools
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get schema for all MCP tools."""
        return [tool.get_schema() for tool in self.mcp_tools.values()]
    
    def connect_all(self):
        """Connect to all MCP servers."""
        results = self.client_manager.connect_all()
        for server_name, connected in results.items():
            if connected:
                client = self.client_manager.get_client(server_name)
                if client:
                    self._load_tools_from_server(server_name, client)
        return results
    
    def disconnect_all(self):
        """Disconnect from all MCP servers."""
        self.client_manager.disconnect_all()
        self.mcp_tools.clear()
    
    def __enter__(self):
        """Context manager entry."""
        self.connect_all()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect_all()

