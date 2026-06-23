"""MCP (Model Context Protocol) client implementation."""
import json
import logging
import subprocess
import threading
import queue
import time
from typing import Dict, Any, List, Optional, Callable
from enum import Enum

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)


class MCPTransportType(Enum):
    """MCP transport types."""
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPClient:
    """Client for connecting to MCP servers."""
    
    def __init__(self, server_name: str, transport_type: str = "stdio", 
                 command: Optional[str] = None, args: Optional[List[str]] = None,
                 url: Optional[str] = None, headers: Optional[Dict[str, str]] = None):
        """
        Initialize MCP client.
        
        Args:
            server_name: Name of the MCP server
            transport_type: Transport type ('stdio', 'http', 'sse')
            command: Command to run for stdio transport
            args: Arguments for the command
            url: URL for HTTP/SSE transport
            headers: Headers for HTTP/SSE transport
        """
        self.server_name = server_name
        self.transport_type = MCPTransportType(transport_type)
        self.command = command
        self.args = args or []
        self.url = url
        self.headers = headers or {}
        
        self.process = None
        self.request_id = 0
        self.pending_requests = {}
        self.request_lock = threading.Lock()
        self.connected = False
        self.tools = {}
        self.resources = {}
        
        # For stdio transport
        self.stdin_queue = queue.Queue()
        self.stdout_thread = None
        self.stderr_thread = None
        
    def connect(self) -> bool:
        """Connect to the MCP server."""
        try:
            if self.transport_type == MCPTransportType.STDIO:
                return self._connect_stdio()
            elif self.transport_type == MCPTransportType.HTTP:
                return self._connect_http()
            elif self.transport_type == MCPTransportType.SSE:
                return self._connect_sse()
            else:
                logger.error(f"Unsupported transport type: {self.transport_type}")
                return False
        except Exception as e:
            logger.error(f"Error connecting to MCP server {self.server_name}: {str(e)}", exc_info=True)
            return False
    
    def _validate_command(self, command: str, args: List[str]) -> bool:
        """Validate command and arguments for security."""
        # Block dangerous commands
        dangerous_commands = ['rm', 'del', 'format', 'mkfs', 'dd', 'shutdown', 'reboot', 'halt']
        if any(cmd in command.lower() for cmd in dangerous_commands):
            logger.error(f"[MCPClient] Dangerous command blocked: {command}")
            return False
        
        # Block shell metacharacters in command
        dangerous_chars = [';', '&', '|', '`', '$', '(', ')', '<', '>', '\n', '\r']
        if any(char in command for char in dangerous_chars):
            logger.error(f"[MCPClient] Dangerous characters in command: {command}")
            return False
        
        # Validate arguments
        for arg in args:
            if any(char in arg for char in dangerous_chars):
                logger.error(f"[MCPClient] Dangerous characters in argument: {arg}")
                return False
        
        return True
    
    def _connect_stdio(self) -> bool:
        """Connect via stdio transport."""
        if not self.command:
            logger.error("Command is required for stdio transport")
            return False
        
        # Security: Validate command and arguments
        if not self._validate_command(self.command, self.args):
            logger.error(f"[MCPClient] Command validation failed: {self.command}")
            return False
        
        try:
            # Start the MCP server process
            # Use list form (not shell=True) to prevent command injection
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,
                shell=False  # Explicitly disable shell to prevent injection
            )
            
            # Start threads to handle stdout and stderr
            self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
            self.stdout_thread.start()
            
            self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            self.stderr_thread.start()
            
            # Initialize the connection
            result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "clientInfo": {
                    "name": "oshani-agent",
                    "version": "1.0.0"
                }
            })
            
            if result and not result.get("error"):
                # List available tools
                self._list_tools()
                self.connected = True
                logger.info(f"Connected to MCP server: {self.server_name}")
                return True
            else:
                logger.error(f"Failed to initialize MCP server: {result}")
                return False
                
        except Exception as e:
            logger.error(f"Error starting MCP server process: {str(e)}", exc_info=True)
            return False
    
    def _connect_http(self) -> bool:
        """Connect via HTTP transport."""
        if not self.url:
            logger.error("URL is required for HTTP transport")
            return False
        
        if not requests:
            logger.error("requests library not available for HTTP transport")
            return False
        
        try:
            # Ensure URL has proper protocol
            url = self.url
            if not url.startswith(('http://', 'https://')):
                url = f"http://{url}"
            
            # Normalize URL: remove trailing /mcp if present (client will append it)
            url = url.rstrip('/')
            if url.endswith('/mcp'):
                url = url[:-4]  # Remove '/mcp' suffix
            
            # Test connection by sending initialize request
            initialize_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "clientInfo": {
                        "name": "oshani-agent",
                        "version": "1.0.0"
                    }
                }
            }
            
            # Prepare headers with authentication if needed
            headers = {
                "Content-Type": "application/json",
                **self.headers
            }
            
            response = requests.post(
                f"{url}/mcp",
                json=initialize_payload,
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("result") and not result.get("error"):
                    self.connected = True
                    logger.info(f"Connected to MCP server via HTTP: {self.server_name}")
                    # List available tools
                    self._list_tools()
                    return True
                elif result.get("error"):
                    error_code = result.get("error", {}).get("code")
                    error_msg = result.get("error", {}).get("message", "")
                    if error_code == -32001:  # Authentication required
                        logger.warning(f"MCP server requires authentication: {error_msg}")
                        logger.warning("Add API key to MCP server headers configuration")
                    else:
                        logger.error(f"Failed to initialize MCP server: {error_msg}")
                    return False
                else:
                    logger.error(f"Failed to initialize MCP server: {result}")
                    return False
            elif response.status_code == 401:
                logger.warning(f"Authentication required for MCP server {self.server_name}")
                logger.warning("Add API key to headers in MCP server configuration")
                return False
            else:
                logger.error(f"HTTP connection failed with status {response.status_code}: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP connection error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error connecting via HTTP: {str(e)}", exc_info=True)
            return False
    
    def _connect_sse(self) -> bool:
        """Connect via SSE transport."""
        # SSE transport implementation would go here
        logger.warning("SSE transport not yet implemented")
        return False
    
    def _read_stdout(self):
        """Read from stdout and process messages."""
        buffer = ""
        while self.process and self.process.poll() is None:
            try:
                char = self.process.stdout.read(1)
                if not char:
                    break
                buffer += char
                
                # Try to parse JSON messages (MCP uses newline-delimited JSON)
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line.strip():
                        try:
                            message = json.loads(line)
                            self._handle_message(message)
                        except json.JSONDecodeError:
                            logger.debug(f"Failed to parse message: {line}")
            except Exception as e:
                logger.error(f"Error reading stdout: {str(e)}")
                break
        
        self.connected = False
        logger.info(f"Disconnected from MCP server: {self.server_name}")
    
    def _read_stderr(self):
        """Read from stderr for logging."""
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
                logger.debug(f"MCP server {self.server_name} stderr: {line.strip()}")
            except Exception as e:
                logger.error(f"Error reading stderr: {str(e)}")
                break
    
    def _handle_message(self, message: Dict[str, Any]):
        """Handle incoming message from MCP server."""
        if "id" in message:
            # Response to a request
            request_id = message["id"]
            with self.request_lock:
                if request_id in self.pending_requests:
                    self.pending_requests[request_id] = message
        elif "method" in message:
            # Notification from server
            method = message.get("method")
            if method == "tools/list":
                # Server is sending tool list
                params = message.get("params", {})
                tools = params.get("tools", [])
                for tool in tools:
                    self.tools[tool.get("name")] = tool
            elif method == "notifications/progress":
                # Progress notification
                logger.debug(f"Progress: {message.get('params', {})}")
    
    def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Send a request to the MCP server."""
        if not self.connected and self.transport_type == MCPTransportType.STDIO:
            if not self.process:
                return None
        
        with self.request_lock:
            self.request_id += 1
            request_id = self.request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        # Send request based on transport type
        if self.transport_type == MCPTransportType.HTTP:
            return self._send_http_request(request)
        elif self.transport_type == MCPTransportType.STDIO:
            if not self.process or not self.process.stdin:
                return None
            try:
                request_json = json.dumps(request) + "\n"
                self.process.stdin.write(request_json)
                self.process.stdin.flush()
            except Exception as e:
                logger.error(f"Error sending request: {str(e)}")
                return None
        else:
            logger.error(f"Unsupported transport type for request: {self.transport_type}")
            return None
        
        # Wait for response (with timeout) - only for STDIO
        if self.transport_type == MCPTransportType.STDIO:
            with self.request_lock:
                self.pending_requests[request_id] = None
            
            timeout = 30  # 30 seconds timeout
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                with self.request_lock:
                    response = self.pending_requests.get(request_id)
                    if response is not None:
                        del self.pending_requests[request_id]
                        return response
                time.sleep(0.1)
            
            # Timeout
            with self.request_lock:
                if request_id in self.pending_requests:
                    del self.pending_requests[request_id]
            
            logger.warning(f"Request timeout for method: {method}")
            return {"error": {"code": -32000, "message": "Request timeout"}}
        
        return None
    
    def _send_http_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send HTTP request to MCP server."""
        if not requests:
            logger.error("requests library not available for HTTP transport")
            return {"error": {"code": -1, "message": "requests library not installed"}}
        
        try:
            # Ensure URL has proper protocol
            url = self.url
            if not url.startswith(('http://', 'https://')):
                url = f"http://{url}"
            
            # Normalize URL: remove trailing /mcp if present (client will append it)
            url = url.rstrip('/')
            if url.endswith('/mcp'):
                url = url[:-4]  # Remove '/mcp' suffix
            
            # Send POST request to /mcp endpoint
            response = requests.post(
                f"{url}/mcp",
                json=request,
                headers={
                    "Content-Type": "application/json",
                    **self.headers
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("error"):
                    logger.error(f"MCP server error: {result.get('error')}")
                return result
            else:
                logger.error(f"HTTP request failed with status {response.status_code}: {response.text}")
                return {"error": {"code": response.status_code, "message": response.text}}
                
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error: {str(e)}")
            return {"error": {"code": -1, "message": str(e)}}
        except Exception as e:
            logger.error(f"Error sending HTTP request: {str(e)}", exc_info=True)
            return {"error": {"code": -1, "message": str(e)}}
    
    def _list_tools(self):
        """List available tools from the MCP server."""
        result = self._send_request("tools/list")
        if result and not result.get("error"):
            tools = result.get("result", {}).get("tools", [])
            for tool in tools:
                self.tools[tool.get("name")] = tool
            logger.info(f"Loaded {len(self.tools)} tools from MCP server {self.server_name}")
        else:
            logger.warning(f"Failed to list tools: {result}")
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """Get list of available tools."""
        return list(self.tools.values())
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on the MCP server."""
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not found"}
        
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        
        if result and not result.get("error"):
            return result.get("result", {})
        else:
            error = result.get("error", {}) if result else {"message": "Unknown error"}
            return {"error": error.get("message", "Tool call failed")}
    
    def disconnect(self):
        """Disconnect from the MCP server."""
        self.connected = False
        
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception as e:
                logger.error(f"Error disconnecting: {str(e)}")
            finally:
                self.process = None
        
        self.tools = {}
        self.resources = {}
        logger.info(f"Disconnected from MCP server: {self.server_name}")
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()


class MCPClientManager:
    """Manager for multiple MCP clients."""
    
    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}
    
    def add_client(self, server_name: str, client: MCPClient):
        """Add an MCP client."""
        self.clients[server_name] = client
    
    def connect_all(self) -> Dict[str, bool]:
        """Connect all clients."""
        results = {}
        for name, client in self.clients.items():
            results[name] = client.connect()
        return results
    
    def get_client(self, server_name: str) -> Optional[MCPClient]:
        """Get a client by name."""
        return self.clients.get(server_name)
    
    def get_all_tools(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all tools from all clients."""
        all_tools = {}
        for name, client in self.clients.items():
            if client.connected:
                all_tools[name] = client.list_tools()
        return all_tools
    
    def disconnect_all(self):
        """Disconnect all clients."""
        for client in self.clients.values():
            try:
                client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting client: {str(e)}")
        self.clients.clear()

