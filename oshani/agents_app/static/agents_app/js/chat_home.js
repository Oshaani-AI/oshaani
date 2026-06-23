    // Handle Conversations collapse toggle icon
    const conversationsCollapse = document.getElementById('conversationsCollapse');
    const sidebarHeader = document.querySelector('.sidebar-header[data-bs-target="#conversationsCollapse"]');
    
    if (conversationsCollapse && sidebarHeader) {
        conversationsCollapse.addEventListener('show.bs.collapse', function () {
            sidebarHeader.setAttribute('aria-expanded', 'true');
        });
        conversationsCollapse.addEventListener('hide.bs.collapse', function () {
            sidebarHeader.setAttribute('aria-expanded', 'false');
        });
    }
    
    // Sidebar column toggle function
    function toggleSidebar() {
        const sidebarColumn = document.getElementById('sidebarColumn');
        const mainChatColumn = document.getElementById('mainChatColumn');
        const toggleBtn = document.getElementById('sidebarToggleBtn');
        
        if (sidebarColumn && mainChatColumn && toggleBtn) {
            const isCollapsed = sidebarColumn.classList.toggle('collapsed');
            mainChatColumn.classList.toggle('expanded', isCollapsed);
            toggleBtn.classList.toggle('collapsed', isCollapsed);
            
            // Save preference to localStorage
            localStorage.setItem('chatSidebarCollapsed', isCollapsed ? 'true' : 'false');
        }
    }
    
    // Show Conversation sidebar by default (do not restore collapsed state from localStorage)
    
    // Hide the global left sidebar on the chat page
    document.body.classList.add('chat-page-no-sidebar');
    window.addEventListener('beforeunload', function () {
        document.body.classList.remove('chat-page-no-sidebar');
    });

    // Check if in view mode (read-only)
    const isViewMode = !!(window.CHAT_CONFIG && window.CHAT_CONFIG.isViewMode);
    
    // Configure marked.js for markdown rendering
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            breaks: true,  // Convert \n to <br>
            gfm: true,     // GitHub Flavored Markdown
            headerIds: false,
            mangle: false
        });
    }
    
    // Function to render markdown
    function renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            try {
                let html = marked.parse(text);
                // Highlight code blocks with Prism.js
                if (typeof Prism !== 'undefined') {
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = html;
                    // Find all code blocks
                    const codeBlocks = tempDiv.querySelectorAll('pre code');
                    codeBlocks.forEach((block) => {
                        // Add language class if not present (for autoloader)
                        if (!block.className) {
                            block.className = 'language-text';
                        }
                        // Highlight with Prism
                        Prism.highlightElement(block);
                    });
                    html = tempDiv.innerHTML;
                }
                return html;
            } catch (e) {
                console.error('Error rendering markdown:', e);
                // Fallback to plain text with line breaks
                return text.replace(/\n/g, '<br>');
            }
        } else {
            // Fallback to plain text with line breaks if marked.js is not loaded
            return text.replace(/\n/g, '<br>');
        }
    }
    
    // Function to re-highlight code blocks after DOM insertion (for dynamically added messages)
    function highlightCodeBlocks(container) {
        if (typeof Prism !== 'undefined') {
            const codeBlocks = container.querySelectorAll('pre code');
            codeBlocks.forEach((block) => {
                if (!block.className) {
                    block.className = 'language-text';
                }
                Prism.highlightElement(block);
            });
        }
    }
    
    // Function to extract reasoning content from message
    function extractReasoning(content) {
        if (!content) return { cleanedContent: content, reasoning: null };
        
        // Match <reasoning>...</reasoning> tags (case-insensitive, handles whitespace)
        const reasoningRegex = /<reasoning[^>]*>([\s\S]*?)<\/reasoning>/gi;
        const reasoningMatches = [];
        let cleanedContent = content;
        let match;
        
        // Extract all reasoning blocks
        while ((match = reasoningRegex.exec(content)) !== null) {
            reasoningMatches.push(match[1].trim());
            // Remove reasoning tag from content
            cleanedContent = cleanedContent.replace(match[0], '').trim();
        }
        
        // If multiple reasoning blocks found, combine them
        const reasoning = reasoningMatches.length > 0 ? reasoningMatches.join('\n\n---\n\n') : null;
        
        return {
            cleanedContent: cleanedContent,
            reasoning: reasoning
        };
    }
    
    // Function to optimize reasoning text (truncate and format)
    function optimizeReasoningText(reasoning) {
        if (!reasoning) return '';
        
        // Remove excessive whitespace
        let optimized = reasoning.replace(/\n{3,}/g, '\n\n').trim();
        
        // If too long, truncate and add indicator
        const maxPreviewLength = 150;
        if (optimized.length > maxPreviewLength) {
            // Try to truncate at sentence boundary
            const truncated = optimized.substring(0, maxPreviewLength);
            const lastPeriod = truncated.lastIndexOf('.');
            const lastNewline = truncated.lastIndexOf('\n');
            const cutPoint = Math.max(lastPeriod, lastNewline);
            
            if (cutPoint > maxPreviewLength * 0.5) {
                return optimized.substring(0, cutPoint + 1) + '...';
            } else {
                return truncated + '...';
            }
        }
        
        return optimized;
    }
    
    // Function to escape HTML for preview text
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // Ensure file/download URLs are absolute (fix relative /media/ links)
    function ensureAbsoluteUrl(url) {
        if (!url || typeof url !== 'string') return url;
        if (url.startsWith('http://') || url.startsWith('https://')) return url;
        return window.location.origin + (url.startsWith('/') ? url : '/' + url);
    }
    
    // Function to create reasoning section HTML
    function createReasoningSection(reasoning, messageId) {
        if (!reasoning) return null;
        
        const optimizedPreview = optimizeReasoningText(reasoning);
        const reasoningId = `reasoning-${messageId}`;
        
        const section = document.createElement('div');
        section.className = 'reasoning-section';
        
        const header = document.createElement('div');
        header.className = 'reasoning-header';
        header.onclick = () => toggleReasoning(reasoningId);
        
        const headerContent = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'reasoning-title';
        title.innerHTML = '<i class="bi bi-chevron-right"></i><span>Reasoning</span>';
        
        const preview = document.createElement('div');
        preview.className = 'reasoning-preview';
        preview.textContent = optimizedPreview;
        
        headerContent.appendChild(title);
        headerContent.appendChild(preview);
        header.appendChild(headerContent);
        
        const content = document.createElement('div');
        content.className = 'reasoning-content';
        content.id = reasoningId;
        const contentInner = document.createElement('div');
        contentInner.className = 'message-content';
        contentInner.innerHTML = renderMarkdown(reasoning);
        content.appendChild(contentInner);
        
        section.appendChild(header);
        section.appendChild(content);
        
        return section;
    }
    
    // Function to render agent list responsively
    function renderAgentList(data) {
        const agents = data.agents || [];
        const count = data.count || agents.length;
        
        if (agents.length === 0) {
            return '<p><em>No agents found.</em></p>';
        }
        
        // Create responsive container
        let html = '<div class="agent-list-container">';
        html += '<div class="agent-list-header">';
        html += `<h4><i class="bi bi-robot"></i> Your Agents</h4>`;
        html += `<span class="agent-list-count">${count} agent${count !== 1 ? 's' : ''}</span>`;
        html += '</div>';
        
        // Render as cards for better mobile responsiveness
        html += '<div class="agent-list-grid">';
        
        agents.forEach(agent => {
            const statusClass = agent.status || 'draft';
            const statusLabel = (agent.status || 'draft').charAt(0).toUpperCase() + (agent.status || 'draft').slice(1);
            const description = agent.description || 'No description';
            const modelName = agent.model_name || 'Not configured';
            const trainingCount = agent.training_data_count || 0;
            const agentType = agent.agent_type || 'general';
            
            html += '<div class="agent-card">';
            html += '<div class="agent-card-header">';
            html += `<h5 class="agent-card-name">${escapeHtml(agent.name || 'Unnamed Agent')}</h5>`;
            html += `<span class="agent-card-status ${statusClass}">${statusLabel}</span>`;
            html += '</div>';
            
            if (description && description !== 'None') {
                html += `<p class="agent-card-description">${escapeHtml(description)}</p>`;
            }
            
            html += '<div class="agent-card-meta">';
            html += `<div class="agent-card-meta-item"><i class="bi bi-cpu"></i> <span>${escapeHtml(modelName)}</span></div>`;
            html += `<div class="agent-card-meta-item"><i class="bi bi-database"></i> <span>${trainingCount} training items</span></div>`;
            html += `<div class="agent-card-meta-item"><i class="bi bi-tag"></i> <span>${escapeHtml(agentType)}</span></div>`;
            if (agent.published_at) {
                const pubDate = new Date(agent.published_at).toLocaleDateString();
                html += `<div class="agent-card-meta-item"><i class="bi bi-calendar-check"></i> <span>Published: ${pubDate}</span></div>`;
            }
            html += '</div>';
            html += '</div>';
        });
        
        html += '</div>'; // Close grid
        html += '</div>'; // Close container
        
        return html;
    }
    
    // Helper function to escape HTML
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // Function to toggle reasoning section
    function toggleReasoning(reasoningId) {
        const content = document.getElementById(reasoningId);
        const header = content.previousElementSibling;
        
        if (content.classList.contains('expanded')) {
            content.classList.remove('expanded');
            header.classList.remove('expanded');
        } else {
            content.classList.add('expanded');
            header.classList.add('expanded');
            
            // Highlight code blocks in reasoning when expanded
            setTimeout(() => {
                highlightCodeBlocks(content);
            }, 100);
        }
    }
    let currentConversationId = null;
    let currentAgentId = null;
    let uploadedFiles = [];  // Array of {file_id, file_name, file_size}
    
    // Speech Recognition variables
    let speechRecognition = null;
    let isRecording = false;
    const speechRecognitionSupported = 'webkitSpeechRecognition' in window || 'SpeechRecognition' in window;
    
    // Initialize Speech Recognition
    function initSpeechRecognition() {
        if (!speechRecognitionSupported) {
            console.log('Speech recognition not supported in this browser');
            const micButton = document.getElementById('micButton');
            if (micButton) {
                micButton.style.display = 'none';
            }
            return;
        }
        
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        speechRecognition = new SpeechRecognition();
        
        // Configure speech recognition
        speechRecognition.continuous = true;  // Keep listening for continuous speech
        speechRecognition.interimResults = true;  // Show interim results while speaking
        speechRecognition.lang = 'en-US';  // Default language
        
        // Event handlers
        speechRecognition.onstart = function() {
            isRecording = true;
            const micButton = document.getElementById('micButton');
            if (micButton) {
                micButton.classList.add('recording');
                micButton.querySelector('i').className = 'bi bi-mic-fill';
                micButton.title = 'Stop recording';
            }
            showSpeechStatus('Listening...');
        };
        
        speechRecognition.onend = function() {
            isRecording = false;
            const micButton = document.getElementById('micButton');
            if (micButton) {
                micButton.classList.remove('recording');
                micButton.querySelector('i').className = 'bi bi-mic';
                micButton.title = 'Voice input';
            }
            hideSpeechStatus();
        };
        
        speechRecognition.onresult = function(event) {
            const messageInput = document.getElementById('messageInput');
            if (!messageInput) return;
            
            let finalTranscript = '';
            let interimTranscript = '';
            
            // Process all results
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    finalTranscript += transcript;
                } else {
                    interimTranscript += transcript;
                }
            }
            
            // Get existing text (up to the last final result)
            const existingText = messageInput.dataset.finalText || '';
            
            if (finalTranscript) {
                // Add space between existing text and new text if needed
                const newFinalText = existingText + (existingText && !existingText.endsWith(' ') ? ' ' : '') + finalTranscript;
                messageInput.value = newFinalText;
                messageInput.dataset.finalText = newFinalText;
            } else if (interimTranscript) {
                // Show interim results with existing final text
                messageInput.value = existingText + (existingText && !existingText.endsWith(' ') ? ' ' : '') + interimTranscript;
            }
            
            // Update status with transcript preview
            if (interimTranscript) {
                showSpeechStatus('Listening: "' + interimTranscript.substring(0, 30) + (interimTranscript.length > 30 ? '...' : '') + '"');
            }
        };
        
        speechRecognition.onerror = function(event) {
            console.error('Speech recognition error:', event.error);
            isRecording = false;
            const micButton = document.getElementById('micButton');
            if (micButton) {
                micButton.classList.remove('recording');
                micButton.querySelector('i').className = 'bi bi-mic';
                micButton.title = 'Voice input';
            }
            
            let errorMessage = 'Speech recognition error';
            switch (event.error) {
                case 'no-speech':
                    errorMessage = 'No speech detected. Try again.';
                    break;
                case 'audio-capture':
                    errorMessage = 'Microphone not available. Check permissions.';
                    break;
                case 'not-allowed':
                    errorMessage = 'Microphone permission denied.';
                    break;
                case 'network':
                    errorMessage = 'Network error. Check connection.';
                    break;
                case 'aborted':
                    // User stopped, no need to show error
                    hideSpeechStatus();
                    return;
            }
            
            showAlert(errorMessage, 'warning');
            hideSpeechStatus();
        };
    }
    
    // Toggle speech recognition on/off
    function toggleSpeechRecognition() {
        if (!speechRecognitionSupported || !speechRecognition) {
            showAlert('Speech recognition is not supported in your browser. Try Chrome or Edge.', 'warning');
            return;
        }
        
        const messageInput = document.getElementById('messageInput');
        
        if (isRecording) {
            // Stop recording
            speechRecognition.stop();
            // Clear the final text tracker
            if (messageInput) {
                delete messageInput.dataset.finalText;
            }
        } else {
            // Start recording
            if (messageInput) {
                // Initialize final text tracker with current input value
                messageInput.dataset.finalText = messageInput.value;
            }
            try {
                speechRecognition.start();
            } catch (e) {
                console.error('Speech recognition start error:', e);
                showAlert('Could not start speech recognition. Please try again.', 'warning');
            }
        }
    }
    
    // Show speech status tooltip
    function showSpeechStatus(message) {
        let statusEl = document.querySelector('.speech-status');
        if (!statusEl) {
            statusEl = document.createElement('div');
            statusEl.className = 'speech-status';
            const micButton = document.getElementById('micButton');
            if (micButton) {
                micButton.style.position = 'relative';
                micButton.appendChild(statusEl);
            }
        }
        statusEl.textContent = message;
        statusEl.classList.add('visible');
    }
    
    // Hide speech status tooltip
    function hideSpeechStatus() {
        const statusEl = document.querySelector('.speech-status');
        if (statusEl) {
            statusEl.classList.remove('visible');
        }
    }
    
    // Initialize speech recognition on page load
    document.addEventListener('DOMContentLoaded', function() {
        initSpeechRecognition();

        // Delegated handler: starter prompt chips → fill input + focus
        document.addEventListener('click', function(e) {
            const chip = e.target.closest('.suggest-chip');
            if (!chip || chip.disabled) return;
            const prompt = chip.getAttribute('data-prompt') || '';
            const messageInput = document.getElementById('messageInput');
            if (!messageInput || messageInput.disabled) return;
            messageInput.value = prompt;
            messageInput.focus();
            try {
                const len = messageInput.value.length;
                messageInput.setSelectionRange(len, len);
            } catch (_) {}
            messageInput.dispatchEvent(new Event('input', { bubbles: true }));
        });
    });
    
    // Get CSRF token
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    
    // Show alert
    function showAlert(message, type = 'success') {
        const alertContainer = document.getElementById('alertContainer');
        const alert = document.createElement('div');
        alert.className = `alert alert-${type} alert-dismissible fade show`;
        alert.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        alertContainer.appendChild(alert);
        setTimeout(() => alert.remove(), 5000);
    }
    
    // Search filter — narrows the visible (already agent-filtered) conversation list
    function filterConversationsBySearch() {
        const input = document.getElementById('conversationSearch');
        const list = document.getElementById('conversationsList');
        const clearBtn = document.getElementById('conversationSearchClear');
        if (!input || !list) return;

        const q = (input.value || '').trim().toLowerCase();
        if (clearBtn) clearBtn.hidden = q.length === 0;

        const items = list.querySelectorAll('.conversation-item[data-conversation-id]');
        let visibleCount = 0;
        items.forEach(item => {
            // Don't show items hidden by agent filter
            if (item.style.display === 'none') {
                item.classList.remove('search-hidden');
                return;
            }
            if (!q) {
                item.classList.remove('search-hidden');
                visibleCount++;
                return;
            }
            const name = (item.dataset.agentName || '').toLowerCase();
            const text = (item.textContent || '').toLowerCase();
            const match = name.includes(q) || text.includes(q);
            item.classList.toggle('search-hidden', !match);
            if (match) visibleCount++;
        });

        let placeholder = document.getElementById('noSearchResultsPlaceholder');
        if (q && visibleCount === 0) {
            if (!placeholder) {
                placeholder = document.createElement('div');
                placeholder.id = 'noSearchResultsPlaceholder';
                placeholder.className = 'no-search-results';
                placeholder.innerHTML = '<i class="bi bi-search"></i>No conversations match your search';
                list.appendChild(placeholder);
            }
            placeholder.style.display = '';
        } else if (placeholder) {
            placeholder.style.display = 'none';
        }
    }

    function clearConversationSearch() {
        const input = document.getElementById('conversationSearch');
        if (!input) return;
        input.value = '';
        filterConversationsBySearch();
        input.focus();
    }

    // Filter conversation list in sidebar by selected agent (show only matching conversations)
    function filterConversationListByAgent() {
        const listContainer = document.getElementById('conversationsList');
        const agentSelect = document.getElementById('agentSelect');
        if (!listContainer || !agentSelect) return;
        
        const agentId = agentSelect.value;
        const items = listContainer.querySelectorAll('.conversation-item[data-agent-id]');
        let visibleCount = 0;
        
        items.forEach(item => {
            const itemAgentId = item.getAttribute('data-agent-id');
            const match = !agentId || itemAgentId === agentId;
            item.style.display = match ? '' : 'none';
            if (match) visibleCount++;
        });
        
        // Show "No conversations for this agent" when an agent is selected but none match
        let placeholder = document.getElementById('noConversationsForAgentPlaceholder');
        if (agentId && visibleCount === 0) {
            if (!placeholder) {
                placeholder = document.createElement('div');
                placeholder.id = 'noConversationsForAgentPlaceholder';
                placeholder.className = 'empty-state';
                placeholder.innerHTML = '<i class="bi bi-chat-dots" style="font-size: 2rem;"></i><p class="mt-2 text-muted small">No conversations for this agent</p>';
                listContainer.appendChild(placeholder);
            }
            placeholder.style.display = 'block';
        } else {
            if (placeholder) placeholder.style.display = 'none';
        }

        // Re-apply search filter on whatever's still visible
        if (typeof filterConversationsBySearch === 'function') {
            try { filterConversationsBySearch(); } catch (_) {}
        }
    }
    
    // Agent selection changed
    function onAgentChange() {
        // Don't allow agent changes in view mode
        if (isViewMode) {
            return;
        }
        
        const agentSelect = document.getElementById('agentSelect');
        if (!agentSelect) return;
        
        const agentId = agentSelect.value;
        const messageInput = document.getElementById('messageInput');
        const sendButton = document.getElementById('sendButton');
        const micButton = document.getElementById('micButton');
        
        if (agentId) {
            currentAgentId = agentId;
            if (messageInput) messageInput.disabled = false;
            if (sendButton) sendButton.disabled = false;
            if (micButton && speechRecognitionSupported) micButton.disabled = false;
            const attachFileBtn = document.getElementById('attachFileBtn');
            if (attachFileBtn) attachFileBtn.disabled = false;
            // Show upload area only when there are attached files (managed by updateFileDisplay)
            if (typeof updateFileDisplay === 'function') updateFileDisplay();
            if (messageInput) messageInput.focus();
            
            // Filter sidebar to show only conversations for this agent
            filterConversationListByAgent();
            
            // Find the latest conversation for this agent (among visible items)
            const latestConversation = document.querySelector(`.conversation-item[data-agent-id="${agentId}"]`);
            
            if (latestConversation) {
                // Load the latest conversation for this agent
                const conversationId = latestConversation.dataset.conversationId;
                loadConversation(conversationId, agentId, latestConversation);
            } else {
                // No existing conversation, clear chat and prepare for new conversation
                clearChat();
                // Show ready state with brand-styled suggestion chips
                const messagesArea = document.getElementById('chatMessages');
                if (messagesArea) {
                    const agentName = agentSelect.options[agentSelect.selectedIndex]?.dataset?.agentName || 'this agent';
                    messagesArea.innerHTML = `
                        <div class="empty-state">
                            <i class="bi bi-stars empty-state-icon"></i>
                            <h4>Ready when you are</h4>
                            <p>Send a message to start chatting with <strong>${escapeHtml(agentName)}</strong>, or pick a starter prompt.</p>
                            <div class="welcome-suggestions" aria-label="Starter prompts">
                                <button type="button" class="suggest-chip" data-prompt="Summarize the key points of this document for me.">
                                    <span class="suggest-icon"><i class="bi bi-file-earmark-text"></i></span>
                                    <span class="suggest-text">
                                        <span class="suggest-title">Summarize a document</span>
                                        <span class="suggest-sub">Distill the key points fast</span>
                                    </span>
                                </button>
                                <button type="button" class="suggest-chip" data-prompt="Help me brainstorm fresh ideas about ">
                                    <span class="suggest-icon"><i class="bi bi-lightbulb"></i></span>
                                    <span class="suggest-text">
                                        <span class="suggest-title">Brainstorm ideas</span>
                                        <span class="suggest-sub">Get creative directions</span>
                                    </span>
                                </button>
                                <button type="button" class="suggest-chip" data-prompt="Explain this concept in simple terms: ">
                                    <span class="suggest-icon"><i class="bi bi-mortarboard"></i></span>
                                    <span class="suggest-text">
                                        <span class="suggest-title">Explain something</span>
                                        <span class="suggest-sub">Break it down clearly</span>
                                    </span>
                                </button>
                                <button type="button" class="suggest-chip" data-prompt="Write code that ">
                                    <span class="suggest-icon"><i class="bi bi-code-slash"></i></span>
                                    <span class="suggest-text">
                                        <span class="suggest-title">Write some code</span>
                                        <span class="suggest-sub">Generate or refactor</span>
                                    </span>
                                </button>
                            </div>
                        </div>
                    `;
                }
            }
            // Enable any starter prompt chips currently visible
            document.querySelectorAll('.suggest-chip').forEach(chip => { chip.disabled = false; });
            // Update webhook configuration link for the selected agent
            updateWebhookLink(agentSelect.options[agentSelect.selectedIndex]);
        } else {
            currentAgentId = null;
            if (messageInput) messageInput.disabled = true;
            if (sendButton) sendButton.disabled = true;
            if (micButton) micButton.disabled = true;
            const attachFileBtn = document.getElementById('attachFileBtn');
            const fileUploadArea = document.getElementById('fileUploadArea');
            if (attachFileBtn) attachFileBtn.disabled = true;
            if (fileUploadArea) fileUploadArea.style.display = 'none';
            clearFiles();
            clearChat();
            // Show all conversations when no agent selected
            filterConversationListByAgent();
            // Reset webhook link
            updateWebhookLink(null);
        }
    }

    // Update the webhook configuration link based on the selected agent
    function updateWebhookLink(option) {
        const link = document.getElementById('agentWebhookLink');
        if (!link) return;
        const slug = option ? (option.dataset?.agentSlug || '') : '';
        const name = option ? (option.dataset?.agentName || '') : '';
        if (slug) {
            link.href = `/dashboard/${encodeURIComponent(slug)}/test/#webhook-section`;
            link.classList.remove('disabled');
            link.removeAttribute('aria-disabled');
            link.removeAttribute('tabindex');
            link.title = `Configure webhook for "${name}" — automate with Zapier, n8n, Make, etc.`;
        } else {
            link.href = '#';
            link.classList.add('disabled');
            link.setAttribute('aria-disabled', 'true');
            link.setAttribute('tabindex', '-1');
            link.title = 'Select an agent to configure its webhook';
        }
    }
    
    // Start new chat
    function startNewChat() {
        // Don't allow starting new chat in view mode
        if (isViewMode) {
            return;
        }
        
        currentConversationId = null;
        clearChat();
        updateConversationList();
        
        // Clear agent selection if needed
        // document.getElementById('agentSelect').value = '';
    }
    
    // Clear chat messages
    function clearChat() {
        const messagesArea = document.getElementById('chatMessages');
        messagesArea.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-chat-dots-fill empty-state-icon"></i>
                <h4 class="mt-3">✨ Welcome to AI Chat ✨</h4>
                <p>
                    <span class="empty-state-highlight">Select an agent</span> from the dropdown above to start a conversation
                </p>
            </div>
        `;
        currentConversationId = null;
        
        // Hide controls and reset pagination
        document.getElementById('chatControls').style.display = 'none';
        totalMessages = 0;
        loadedMessageIds.clear();
        oldestLoadedMessageId = null;
        hasMoreMessages = false;
    }
    
    // Global variables for pagination
    let totalMessages = 0;
    let loadedMessageIds = new Set();
    let oldestLoadedMessageId = null;
    let hasMoreMessages = false;

    // Delete a conversation (hard delete; cascades to messages/files/tool_calls)
    window.deleteConversation = async function(conversationId, btnEl) {
        if (isViewMode) return;
        if (!conversationId) return;

        const item = btnEl ? btnEl.closest('.conversation-item') : document.querySelector(`.conversation-item[data-conversation-id="${conversationId}"]`);
        const agentName = item ? (item.dataset.agentName || 'this conversation') : 'this conversation';

        const confirmed = window.confirm(`Delete conversation with "${agentName}"?\n\nThis cannot be undone. All messages and uploaded files in this conversation will be permanently removed.`);
        if (!confirmed) return;

        if (item) item.classList.add('deleting');
        if (btnEl) btnEl.disabled = true;

        try {
            const response = await fetch(`/api/chat/conversation/${encodeURIComponent(conversationId)}/delete/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken'),
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                },
                credentials: 'same-origin',
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                throw new Error(data.error || `Request failed (${response.status})`);
            }

            const wasActive = (currentConversationId === conversationId);
            if (item) item.remove();

            if (wasActive) {
                currentConversationId = null;
                clearChat();
            }

            const list = document.getElementById('conversationsList');
            if (list && !list.querySelector('.conversation-item')) {
                let placeholder = document.getElementById('noConversationsForAgentPlaceholder');
                if (!placeholder) {
                    placeholder = document.createElement('div');
                    placeholder.className = 'empty-state';
                    placeholder.innerHTML = '<i class="bi bi-chat-dots" style="font-size: 3rem;"></i><p class="mt-3">No conversations yet</p><p class="text-muted">Start a new chat to begin</p>';
                    list.appendChild(placeholder);
                }
            }

            if (typeof filterConversationListByAgent === 'function') {
                try { filterConversationListByAgent(); } catch (_) {}
            }

            showAlert('Conversation deleted', 'success');
        } catch (err) {
            console.error('Failed to delete conversation:', err);
            if (item) item.classList.remove('deleting');
            if (btnEl) btnEl.disabled = false;
            showAlert(`Could not delete conversation: ${err.message || err}`, 'danger');
        }
    };
    
    // Load conversation - make it globally accessible
    window.loadConversation = function(conversationId, agentId, element) {
        // Handle optional element parameter
        if (typeof element === 'undefined') {
            element = null;
        }
        
        currentConversationId = conversationId;
        currentAgentId = agentId;
        
        // Reset pagination state
        totalMessages = 0;
        loadedMessageIds.clear();
        oldestLoadedMessageId = null;
        hasMoreMessages = false;
        
        // Update agent selector (only if not in view mode)
        if (!isViewMode) {
            const agentSelect = document.getElementById('agentSelect');
            if (agentSelect) {
                agentSelect.value = agentId;
            }
            const messageInput = document.getElementById('messageInput');
            const sendButton = document.getElementById('sendButton');
            const micButton = document.getElementById('micButton');
            const attachFileBtn = document.getElementById('attachFileBtn');
            
            if (messageInput) messageInput.disabled = false;
            if (sendButton) sendButton.disabled = false;
            if (micButton && speechRecognitionSupported) micButton.disabled = false;
            if (attachFileBtn) attachFileBtn.disabled = false;
            if (typeof updateFileDisplay === 'function') updateFileDisplay();
        }
        
        // Highlight active conversation
        document.querySelectorAll('.conversation-item').forEach(item => {
            item.classList.remove('active');
        });
        if (element) {
            element.classList.add('active');
        }
        
        // Show controls
        const chatControls = document.getElementById('chatControls');
        if (chatControls) {
            chatControls.style.display = 'block';
        }
        
        // Ensure tool calls button is visible
        const toolCallsBtn = document.getElementById('toolCallsToggleBtn');
        if (toolCallsBtn) {
            toolCallsBtn.style.display = 'inline-block';
        }
        
        // Load tool calls for this conversation
        if (toolCallsPanelVisible) {
            loadToolCalls();
        }
        
        // Load messages (last 20 - most recent)
        fetch(`/api/chat/conversation/${conversationId}/`)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    totalMessages = data.total_messages || 0;
                    hasMoreMessages = data.has_more || false;
                    
                    // Store message IDs and track oldest/newest
                    if (data.messages && data.messages.length > 0) {
                        // Messages are already in chronological order (oldest first, newest last)
                        oldestLoadedMessageId = data.messages[0].id;  // Oldest message
                        // Don't add IDs here - let displayMessages handle it to avoid skipping messages
                    }
                    
                    displayMessages(data.messages, false);
                    updateMessageCount();
                    
                    // Load tool calls count even if panel is not visible
                    loadToolCalls();
                    
                    // Scroll to bottom to show latest messages
                    setTimeout(() => scrollToBottom(), 100);
                } else {
                    showAlert(data.error || 'Failed to load conversation', 'danger');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showAlert('An error occurred while loading the conversation', 'danger');
            });
    }
    
    // Load older messages
    function loadOlderMessages() {
        if (!currentConversationId || !hasMoreMessages) {
            return;
        }
        
        const loadBtn = document.getElementById('loadOlderBtn');
        loadBtn.disabled = true;
        loadBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Loading...';
        
        // Build URL with pagination params
        let url = `/api/chat/conversation/${currentConversationId}/messages/?per_page=20`;
        if (oldestLoadedMessageId) {
            url += `&before_id=${oldestLoadedMessageId}`;
        }
        
        fetch(url)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.messages && data.messages.length > 0) {
                    // Filter out already loaded messages to prevent duplicates
                    const newMessages = data.messages.filter(msg => !loadedMessageIds.has(msg.id));
                    
                    if (newMessages.length > 0) {
                        // Store oldest message ID for next pagination
                        oldestLoadedMessageId = newMessages[0].id;
                        // Add message IDs to prevent duplicates
                        newMessages.forEach(msg => loadedMessageIds.add(msg.id));
                        
                        hasMoreMessages = data.has_more || false;
                        
                        // Prepend older messages
                        displayMessages(newMessages, true);
                        updateMessageCount();
                        
                        // Maintain scroll position
                        const messagesArea = document.getElementById('chatMessages');
                        const scrollHeightBefore = messagesArea.scrollHeight;
                        messagesArea.scrollTop = messagesArea.scrollHeight - scrollHeightBefore;
                    } else {
                        // All messages were already loaded
                        hasMoreMessages = false;
                        loadBtn.style.display = 'none';
                    }
                } else {
                    hasMoreMessages = false;
                }
                
                loadBtn.disabled = false;
                loadBtn.innerHTML = '<i class="bi bi-arrow-up"></i> Load Older Messages';
                
                if (!hasMoreMessages) {
                    loadBtn.style.display = 'none';
                }
            })
            .catch(error => {
                console.error('Error loading older messages:', error);
                showAlert('Failed to load older messages', 'danger');
                loadBtn.disabled = false;
                loadBtn.innerHTML = '<i class="bi bi-arrow-up"></i> Load Older Messages';
            });
    }
    
    // Download chat history
    function downloadChatHistory() {
        if (!currentConversationId) {
            showAlert('No conversation selected', 'warning');
            return;
        }
        
        const downloadBtn = document.getElementById('downloadChatBtn');
        downloadBtn.disabled = true;
        downloadBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Downloading...';
        
        // Trigger download
        window.location.href = `/api/chat/conversation/${currentConversationId}/download/`;
        
        // Re-enable button after a delay
        setTimeout(() => {
            downloadBtn.disabled = false;
            downloadBtn.innerHTML = '<i class="bi bi-download"></i> Download Chat';
        }, 2000);
    }
    
    // Tool Calls Panel Functions
    let toolCallsData = [];
    let toolCallsPanelVisible = false;
    
    function toggleToolCallsPanel() {
        const panel = document.getElementById('toolCallsPanel');
        const button = document.getElementById('toolCallsToggleBtn');
        
        if (!panel) {
            console.error('Tool calls panel not found');
            return;
        }
        
        if (!currentConversationId) {
            showAlert('Please select a conversation first', 'warning');
            return;
        }
        
        toolCallsPanelVisible = !toolCallsPanelVisible;
        panel.style.display = toolCallsPanelVisible ? 'flex' : 'none';
        
        // Update button appearance
        if (button) {
            if (toolCallsPanelVisible) {
                button.classList.add('active');
            } else {
                button.classList.remove('active');
            }
        }
        
        if (toolCallsPanelVisible && currentConversationId) {
            loadToolCalls();
        }
    }
    
    // Make function globally available
    window.toggleToolCallsPanel = toggleToolCallsPanel;
    
    function loadToolCalls() {
        if (!currentConversationId) {
            return;
        }
        
        const contentDiv = document.getElementById('toolCallsContent');
        if (!contentDiv && toolCallsPanelVisible) {
            // Only show loading if panel is visible
            return;
        }
        
        if (contentDiv && toolCallsPanelVisible) {
            contentDiv.innerHTML = '<div class="text-center text-muted"><i class="bi bi-hourglass-split"></i> Loading tool calls...</div>';
        }
        
        fetch(`/api/chat/conversation/${currentConversationId}/tool-calls/`, {
            credentials: 'include',
            headers: {
                'X-CSRFToken': getCookie('csrftoken') || ''
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                toolCallsData = data.tool_calls || [];
                updateToolCallsCount();
                
                // Only update display if panel is visible
                if (toolCallsPanelVisible && contentDiv) {
                    displayToolCalls(toolCallsData);
                }
            } else {
                if (contentDiv && toolCallsPanelVisible) {
                    contentDiv.innerHTML = `<div class="text-center text-danger">Failed to load tool calls: ${data.error || 'Unknown error'}</div>`;
                }
            }
        })
        .catch(error => {
            console.error('Error loading tool calls:', error);
            if (contentDiv && toolCallsPanelVisible) {
                contentDiv.innerHTML = '<div class="text-center text-danger">Failed to load tool calls</div>';
            }
        });
    }
    
    function displayToolCalls(toolCalls) {
        const contentDiv = document.getElementById('toolCallsContent');
        if (!contentDiv) return;
        
        if (toolCalls.length === 0) {
            contentDiv.innerHTML = '<div class="no-tool-calls">No tool calls yet in this conversation.</div>';
            return;
        }
        
        const listDiv = document.createElement('div');
        listDiv.className = 'tool-calls-list';
        
        toolCalls.forEach(toolCall => {
            const itemDiv = document.createElement('div');
            itemDiv.className = `tool-call-item ${toolCall.state}`;
            
            // Header
            const headerDiv = document.createElement('div');
            headerDiv.className = 'tool-call-header';
            
            const nameSpan = document.createElement('span');
            nameSpan.className = 'tool-name';
            nameSpan.innerHTML = `🔧 ${escapeHtml(toolCall.tool_name)}`;
            
            const stateSpan = document.createElement('span');
            stateSpan.className = `tool-state ${toolCall.state}`;
            stateSpan.textContent = toolCall.state;
            
            const timeSpan = document.createElement('span');
            timeSpan.className = 'tool-time';
            if (toolCall.created_at) {
                timeSpan.textContent = new Date(toolCall.created_at).toLocaleString();
            }
            
            headerDiv.appendChild(nameSpan);
            headerDiv.appendChild(stateSpan);
            headerDiv.appendChild(timeSpan);
            itemDiv.appendChild(headerDiv);
            
            // Parameters
            if (toolCall.parameters && Object.keys(toolCall.parameters).length > 0) {
                const paramsSection = document.createElement('div');
                paramsSection.className = 'tool-call-section';
                paramsSection.innerHTML = '<strong>Parameters:</strong>';
                const paramsPre = document.createElement('pre');
                paramsPre.className = 'tool-call-data';
                paramsPre.textContent = JSON.stringify(toolCall.parameters, null, 2);
                paramsSection.appendChild(paramsPre);
                itemDiv.appendChild(paramsSection);
            }
            
            // Result
            if (toolCall.result_content) {
                const resultSection = document.createElement('div');
                resultSection.className = 'tool-call-section';
                resultSection.innerHTML = '<strong>Result:</strong>';
                const resultDiv = document.createElement('div');
                resultDiv.className = 'tool-call-result';
                const resultText = toolCall.result_content.length > 500 
                    ? toolCall.result_content.substring(0, 500) + '...' 
                    : toolCall.result_content;
                resultDiv.textContent = resultText;
                resultSection.appendChild(resultDiv);
                itemDiv.appendChild(resultSection);
            }
            
            // Generated Files
            if (toolCall.result_files && toolCall.result_files.length > 0) {
                const filesSection = document.createElement('div');
                filesSection.className = 'tool-call-section';
                filesSection.innerHTML = '<strong>Generated Files:</strong>';
                const filesUl = document.createElement('ul');
                filesUl.className = 'tool-call-files';
                toolCall.result_files.forEach(file => {
                    const li = document.createElement('li');
                    li.textContent = file.file_name || file;
                    filesUl.appendChild(li);
                });
                filesSection.appendChild(filesUl);
                itemDiv.appendChild(filesSection);
            }
            
            // Error
            if (toolCall.error) {
                const errorSection = document.createElement('div');
                errorSection.className = 'tool-call-section error';
                errorSection.innerHTML = '<strong>Error:</strong>';
                const errorDiv = document.createElement('div');
                errorDiv.className = 'tool-call-error';
                errorDiv.textContent = toolCall.error;
                errorSection.appendChild(errorDiv);
                itemDiv.appendChild(errorSection);
            }
            
            // Footer
            if (toolCall.completed_at) {
                const footerDiv = document.createElement('div');
                footerDiv.className = 'tool-call-footer';
                footerDiv.textContent = `Completed: ${new Date(toolCall.completed_at).toLocaleString()}`;
                itemDiv.appendChild(footerDiv);
            }
            
            listDiv.appendChild(itemDiv);
        });
        
        contentDiv.innerHTML = '';
        contentDiv.appendChild(listDiv);
    }
    
    function updateToolCallsCount() {
        const countSpan = document.getElementById('toolCallsCount');
        const panelCountSpan = document.getElementById('toolCallsPanelCount');
        
        if (countSpan) {
            if (toolCallsData.length > 0) {
                countSpan.textContent = `Tools (${toolCallsData.length})`;
            } else {
                countSpan.textContent = 'Tools';
            }
        }
        
        if (panelCountSpan) {
            panelCountSpan.textContent = toolCallsData.length;
        }
    }
    
    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    }
    
    // Update message count display
    function updateMessageCount() {
        const countElement = document.getElementById('messageCount');
        const loadedCount = loadedMessageIds.size;
        if (totalMessages > 0) {
            countElement.textContent = `Showing ${loadedCount} of ${totalMessages} messages`;
            if (hasMoreMessages) {
                countElement.textContent += ` (${totalMessages - loadedCount} older messages available)`;
            }
        } else {
            countElement.textContent = '';
        }
        
        // Show/hide load older button
        const loadBtn = document.getElementById('loadOlderBtn');
        if (hasMoreMessages) {
            loadBtn.style.display = 'inline-block';
        } else {
            loadBtn.style.display = 'none';
        }
    }
    
    // Display messages (prepend if loading older messages)
    function displayMessages(messages, prepend = false) {
        const messagesArea = document.getElementById('chatMessages');
        
        // Clear empty state if exists
        const emptyState = messagesArea.querySelector('.empty-state');
        if (emptyState) {
            emptyState.remove();
        }
        
        // If prepending, create a temporary container
        const fragment = document.createDocumentFragment();
        
        messages.forEach(msg => {
            // Skip if already loaded - always check to prevent duplicates
            // But allow initial load (when prepend is false) to show all messages
            if (prepend && loadedMessageIds.has(msg.id)) {
                return;
            }
            
            // Add to loaded set to prevent future duplicates
            if (!loadedMessageIds.has(msg.id)) {
                loadedMessageIds.add(msg.id);
            }
            
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${msg.type}`;
            messageDiv.setAttribute('data-message-id', msg.id);
            
            let content = msg.content;
            if (msg.type === 'tool_call' && msg.tool_name) {
                content = `**Tool Call:** ${msg.tool_name}\n\n${content}`;
            } else if (msg.type === 'tool_result' && msg.tool_name) {
                content = `**Tool Result:** ${msg.tool_name}\n\n${content}`;
            }
            
            // Check if this is an agent list result from mcp_Oshaani AI_list_agents
            let agentListData = null;
            if (msg.type === 'tool_result' && msg.tool_name && 
                (msg.tool_name.includes('list_agents') || msg.tool_name.includes('list_agent'))) {
                try {
                    // Try to parse JSON from content
                    const jsonMatch = content.match(/\{[\s\S]*\}/);
                    if (jsonMatch) {
                        const parsed = JSON.parse(jsonMatch[0]);
                        if (parsed.agents && Array.isArray(parsed.agents)) {
                            agentListData = parsed;
                        }
                    }
                } catch (e) {
                    // Not JSON or parse failed, continue with normal rendering
                }
            }
            
            // Extract reasoning from content
            const { cleanedContent, reasoning } = extractReasoning(content);
            
            // Display tool calls before message content (transparent, detailed)
            if (msg.type === 'agent' && msg.tool_calls && msg.tool_calls.length > 0) {
                const toolCallsContainer = document.createElement('div');
                toolCallsContainer.className = 'tool-calls-inline';
                
                const sectionTitle = document.createElement('div');
                sectionTitle.className = 'tool-calls-inline-title';
                sectionTitle.innerHTML = '<i class="bi bi-tools"></i> Tools used (' + msg.tool_calls.length + ')';
                toolCallsContainer.appendChild(sectionTitle);
                
                msg.tool_calls.forEach((tool, idx) => {
                    const toolDetail = document.createElement('div');
                    toolDetail.className = 'tool-call-detail';
                    
                    const toolName = tool.tool || tool.tool_name || 'unknown';
                    const parameters = tool.parameters || {};
                    const rawResult = tool.result !== undefined ? tool.result : (tool.result_content !== undefined ? tool.result_content : '');
                    const resultFilesFromResult = (rawResult && typeof rawResult === 'object' && rawResult.result_files) ? rawResult.result_files : [];
                    const resultFilesList = tool.result_files || resultFilesFromResult || [];
                    
                    // Resolve result text: support object with result_content, error, or plain string
                    let resultText = '';
                    let isError = false;
                    if (rawResult !== undefined && rawResult !== null && rawResult !== '') {
                        if (typeof rawResult === 'string') {
                            resultText = rawResult;
                        } else if (typeof rawResult === 'object') {
                            if (rawResult.error) {
                                resultText = rawResult.error;
                                isError = true;
                            } else if (rawResult.result_content !== undefined) {
                                resultText = typeof rawResult.result_content === 'string' ? rawResult.result_content : JSON.stringify(rawResult.result_content, null, 2);
                            } else if (rawResult.result !== undefined) {
                                resultText = typeof rawResult.result === 'string' ? rawResult.result : JSON.stringify(rawResult.result, null, 2);
                            } else {
                                resultText = JSON.stringify(rawResult, null, 2);
                            }
                        }
                    }
                    const resultFilesArray = Array.isArray(resultFilesList) ? resultFilesList : [];
                    
                    // Header
                    const header = document.createElement('div');
                    header.className = 'tool-call-header-inline';
                    header.innerHTML = '<span class="tool-icon">🔧</span><span class="tool-name-inline">' + escapeHtml(toolName) + '</span>';
                    toolDetail.appendChild(header);
                    
                    // Parameters (collapsible)
                    if (Object.keys(parameters).length > 0) {
                        const paramsLabel = document.createElement('div');
                        paramsLabel.className = 'tool-call-section-label';
                        paramsLabel.textContent = 'Parameters';
                        toolDetail.appendChild(paramsLabel);
                        const paramsDetails = document.createElement('details');
                        paramsDetails.className = 'tool-call-params';
                        paramsDetails.innerHTML = '<summary>View parameters</summary>';
                        const paramsPre = document.createElement('pre');
                        paramsPre.className = 'tool-call-params-content';
                        paramsPre.textContent = JSON.stringify(parameters, null, 2);
                        paramsDetails.appendChild(paramsPre);
                        toolDetail.appendChild(paramsDetails);
                    }
                    
                    // Result (expandable, full content)
                    if (resultText) {
                        const resultLabel = document.createElement('div');
                        resultLabel.className = 'tool-call-section-label';
                        resultLabel.textContent = isError ? 'Error' : 'Result';
                        const resultDiv = document.createElement('div');
                        resultDiv.className = 'tool-call-result-inline' + (isError ? ' tool-result-error' : '');
                        resultDiv.appendChild(resultLabel);
                        const resultDetails = document.createElement('details');
                        resultDetails.className = 'tool-call-params';
                        resultDetails.setAttribute('open', 'open');
                        resultDetails.innerHTML = '<summary>View ' + (isError ? 'error' : 'result') + '</summary>';
                        const resultContent = document.createElement('pre');
                        resultContent.className = 'tool-result-content';
                        resultContent.textContent = resultText;
                        resultDetails.appendChild(resultContent);
                        resultDiv.appendChild(resultDetails);
                        toolDetail.appendChild(resultDiv);
                    }
                    
                    // Files (clickable links)
                    if (resultFilesArray.length > 0) {
                        const filesLabel = document.createElement('div');
                        filesLabel.className = 'tool-call-section-label';
                        filesLabel.textContent = 'Output files';
                        const filesDiv = document.createElement('div');
                        filesDiv.className = 'tool-call-files-inline';
                        filesDiv.appendChild(filesLabel);
                        const filesUl = document.createElement('ul');
                        resultFilesArray.forEach(function(file) {
                            const li = document.createElement('li');
                            const name = file.file_name || file.name || (typeof file === 'string' ? file : 'file');
                            const url = file.file_url || file.url || (typeof file === 'string' ? file : null);
                            if (url) {
                                const a = document.createElement('a');
                                a.href = ensureAbsoluteUrl(url);
                                a.target = '_blank';
                                a.rel = 'noopener';
                                a.textContent = name;
                                li.appendChild(a);
                            } else {
                                li.textContent = name;
                            }
                            filesUl.appendChild(li);
                        });
                        filesDiv.appendChild(filesUl);
                        toolDetail.appendChild(filesDiv);
                    }
                    
                    toolCallsContainer.appendChild(toolDetail);
                });
                
                messageDiv.appendChild(toolCallsContainer);
            }
            
            // Create content container
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            
            // If agent list data found, render it responsively
            if (agentListData && agentListData.agents) {
                const agentListHtml = renderAgentList(agentListData);
                contentDiv.innerHTML = agentListHtml;
            } else {
                // Normal markdown rendering
                contentDiv.innerHTML = renderMarkdown(cleanedContent);
            }
            
            messageDiv.appendChild(contentDiv);
            
            // Add reasoning section if present (for agent messages)
            if (reasoning && msg.type === 'agent') {
                const reasoningSection = createReasoningSection(reasoning, msg.id);
                if (reasoningSection) {
                    messageDiv.appendChild(reasoningSection);
                }
            }
            
            // Display files if any (for agent messages with generated files)
            if (msg.files && msg.files.length > 0) {
                const filesContainer = document.createElement('div');
                filesContainer.className = 'message-files';
                filesContainer.style.marginTop = '10px';
                filesContainer.style.display = 'flex';
                filesContainer.style.flexWrap = 'wrap';
                filesContainer.style.gap = '8px';
                
                msg.files.forEach(file => {
                    const fileUrl = ensureAbsoluteUrl(file.file_url);
                    const fileElement = document.createElement('div');
                    fileElement.style.display = 'inline-block';
                    
                    // Check if it's an image
                    if (file.file_type && file.file_type.startsWith('image/')) {
                        const img = document.createElement('img');
                        img.src = fileUrl;
                        img.alt = file.file_name;
                        img.style.maxWidth = '300px';
                        img.style.maxHeight = '300px';
                        img.style.borderRadius = '8px';
                        img.style.cursor = 'pointer';
                        img.onclick = () => window.open(fileUrl, '_blank');
                        img.title = `Click to view full size: ${file.file_name}`;
                        fileElement.appendChild(img);
                    } else {
                        // For non-image files, show a download link
                        const fileLink = document.createElement('a');
                        fileLink.href = fileUrl;
                        fileLink.download = file.file_name;  // Suggest filename for download
                        fileLink.target = '_blank';
                        fileLink.className = 'file-item';
                        fileLink.style.textDecoration = 'none';
                        fileLink.style.cursor = 'pointer';
                        fileLink.title = `Download ${file.file_name}`;
                        fileLink.innerHTML = `<i class="bi bi-file-earmark"></i> ${file.file_name} <i class="bi bi-download" style="margin-left: 4px;"></i>`;
                        
                        // Force download by adding click handler as fallback
                        fileLink.onclick = function(e) {
                            // Try programmatic download if download attribute doesn't work
                            // This handles cases where browser blocks download attribute
                            setTimeout(() => {
                                // Check if download started, if not, try programmatic download
                                fetch(fileUrl, {
                                    method: 'HEAD',
                                    credentials: 'include'
                                })
                                .then(response => {
                                    if (response.ok) {
                                        // File exists, try full download
                                        return fetch(fileUrl, {
                                            method: 'GET',
                                            credentials: 'include'
                                        });
                                    }
                                    throw new Error('File not accessible');
                                })
                                .then(response => response.blob())
                                .then(blob => {
                                    const url = window.URL.createObjectURL(blob);
                                    const a = document.createElement('a');
                                    a.href = url;
                                    a.download = file.file_name;
                                    document.body.appendChild(a);
                                    a.click();
                                    window.URL.revokeObjectURL(url);
                                    document.body.removeChild(a);
                                })
                                .catch(error => {
                                    // Silently fail - let browser handle it normally
                                    console.debug('Programmatic download not needed:', error);
                                });
                            }, 100);
                        };
                        
                        fileElement.appendChild(fileLink);
                    }
                    
                    filesContainer.appendChild(fileElement);
                });
                
                messageDiv.appendChild(filesContainer);
            }
            
            // Add feedback buttons for agent messages
            if (msg.type === 'agent' && msg.id) {
                const feedbackDiv = document.createElement('div');
                feedbackDiv.className = 'message-feedback';
                feedbackDiv.innerHTML = `
                    <button class="feedback-btn positive" onclick="submitFeedback(${msg.id}, 'positive', this)" data-message-id="${msg.id}">
                        <i class="bi bi-hand-thumbs-up"></i> Helpful
                    </button>
                    <button class="feedback-btn negative" onclick="submitFeedback(${msg.id}, 'negative', this)" data-message-id="${msg.id}">
                        <i class="bi bi-hand-thumbs-down"></i> Not Helpful
                    </button>
                `;
                messageDiv.appendChild(feedbackDiv);
            }
            
            fragment.appendChild(messageDiv);
        });
        
        if (prepend) {
            // Prepend older messages at the top
            messagesArea.insertBefore(fragment, messagesArea.firstChild);
            // Re-highlight code blocks after prepending
            setTimeout(() => highlightCodeBlocks(messagesArea), 50);
        } else {
            // Replace all messages (initial load) - ensure we show latest messages
            messagesArea.innerHTML = '';
            messagesArea.appendChild(fragment);
            // Re-highlight code blocks after appending
            setTimeout(() => highlightCodeBlocks(messagesArea), 50);
        }
        
        if (messagesArea.children.length === 0) {
            messagesArea.innerHTML = '<div class="empty-state"><p class="text-muted">No messages yet. Start the conversation!</p></div>';
        } else if (!prepend) {
            // After initial load, scroll to bottom to show latest messages
            setTimeout(() => scrollToBottom(), 100);
        }
    }
    
    // Render live tool calls into a container (for polling updates)
    function renderLiveToolCalls(container, toolCalls) {
        if (!container || !Array.isArray(toolCalls) || toolCalls.length === 0) {
            if (container) container.innerHTML = '';
            return;
        }
        const parts = [];
        parts.push('<div class="tool-calls-inline-title"><i class="bi bi-tools"></i> Tools used (' + toolCalls.length + ')</div>');
        toolCalls.forEach(function(tc) {
            const name = escapeHtml(tc.tool || tc.tool_name || 'unknown');
            const state = tc.state || 'done';
            const params = tc.parameters || {};
            const result = tc.result || {};
            const paramsJson = JSON.stringify(params, null, 2);
            let resultText = result.result_content !== undefined ? (typeof result.result_content === 'string' ? result.result_content : JSON.stringify(result.result_content, null, 2)) : (result.error || JSON.stringify(result, null, 2));
            const isError = !!result.error;
            const resultFiles = result.result_files || [];
            parts.push('<div class="tool-call-detail ' + state + '">');
            parts.push('<div class="tool-call-header-inline">');
            parts.push('<span class="tool-icon">' + (state === 'executing' ? '&#9203;' : '&#128295;') + '</span>');
            parts.push('<span class="tool-name-inline">' + name + '</span>');
            if (state === 'executing') parts.push('<span class="tool-executing-indicator">Executing...</span>');
            if (state === 'done') parts.push('<span class="tool-status-badge done">&#10003; Done</span>');
            parts.push('</div>');
            if (Object.keys(params).length > 0) {
                parts.push('<div class="tool-call-section-label">Parameters</div>');
                parts.push('<details class="tool-call-params"><summary>View parameters</summary>');
                parts.push('<pre class="tool-call-params-content">' + escapeHtml(paramsJson) + '</pre></details>');
            }
            if (resultText) {
                parts.push('<div class="tool-call-result-inline' + (isError ? ' tool-result-error' : '') + '">');
                parts.push('<div class="tool-call-section-label">' + (isError ? 'Error' : 'Result') + '</div>');
                parts.push('<details class="tool-call-params" open><summary>View ' + (isError ? 'error' : 'result') + '</summary>');
                parts.push('<pre class="tool-result-content">' + escapeHtml(resultText) + '</pre></details></div>');
            }
            if (resultFiles.length > 0) {
                parts.push('<div class="tool-call-section-label">Output files</div>');
                parts.push('<div class="tool-call-files-inline"><ul>');
                resultFiles.forEach(function(f) {
                    const fn = f.file_name || f.name || (typeof f === 'string' ? f : 'file');
                    const url = f.file_url || f.url;
                    parts.push('<li>' + (url ? '<a href="' + escapeHtml(ensureAbsoluteUrl(url)) + '" target="_blank" rel="noopener">' + escapeHtml(fn) + '</a>' : escapeHtml(fn)) + '</li>');
                });
                parts.push('</ul></div>');
            }
            parts.push('</div>');
        });
        container.innerHTML = parts.join('');
    }

    // Poll background chat task until complete; show live tool calls while running
    function pollChatTask(taskId, messageInput, messagesArea) {
        const pollInterval = 1000;
        const maxAttempts = 600;
        let attempts = 0;
        var placeholder = null;

        function poll() {
            attempts++;
            fetch(`/api/chat/task-status/${taskId}/`, { credentials: 'include' })
                .then(r => r.json())
                .then(data => {
                    if (data.success && data.status === 'running' && data.result) {
                        placeholder = messagesArea.querySelector('.message.agent.pending[data-task-id="' + taskId + '"]');
                        if (placeholder) {
                            var liveEl = placeholder.querySelector('.live-tool-calls');
                            if (liveEl) renderLiveToolCalls(liveEl, data.result.tool_calls || []);
                            if (data.result.response) {
                                var contentEl = placeholder.querySelector('.message-content');
                                if (contentEl) contentEl.innerHTML = escapeHtml(data.result.response).substring(0, 500) + (data.result.response.length > 500 ? '...' : '');
                            }
                        }
                        setTimeout(poll, pollInterval);
                        return;
                    }
                    if (data.success && data.status === 'success' && data.result) {
                        document.getElementById('typingIndicator').classList.remove('active');
                        placeholder = messagesArea.querySelector('.message.agent.pending[data-task-id="' + taskId + '"]');
                        if (placeholder) placeholder.remove();
                        applyChatResult(data.result, messageInput, messagesArea);
                        return;
                    }
                    if (data.success && data.status === 'failure' && data.result) {
                        document.getElementById('typingIndicator').classList.remove('active');
                        placeholder = messagesArea.querySelector('.message.agent.pending[data-task-id="' + taskId + '"]');
                        if (placeholder) placeholder.remove();
                        showAlert((data.result && data.result.error) || 'Request failed', 'danger');
                        messageInput.disabled = false;
                        document.getElementById('sendButton').disabled = false;
                        messageInput.focus();
                        return;
                    }
                    if (attempts >= maxAttempts) {
                        document.getElementById('typingIndicator').classList.remove('active');
                        placeholder = messagesArea.querySelector('.message.agent.pending[data-task-id="' + taskId + '"]');
                        if (placeholder) placeholder.remove();
                        showAlert('Request timed out. Check the conversation for the latest message.', 'warning');
                        messageInput.disabled = false;
                        document.getElementById('sendButton').disabled = false;
                        messageInput.focus();
                        if (currentConversationId) {
                            loadConversation(currentConversationId, currentAgentId, null);
                        }
                        return;
                    }
                    setTimeout(poll, pollInterval);
                })
                .catch(err => {
                    console.error('Poll error:', err);
                    document.getElementById('typingIndicator').classList.remove('active');
                    placeholder = messagesArea.querySelector('.message.agent.pending[data-task-id="' + taskId + '"]');
                    if (placeholder) placeholder.remove();
                    showAlert('Error checking response status', 'danger');
                    messageInput.disabled = false;
                    document.getElementById('sendButton').disabled = false;
                    messageInput.focus();
                });
        }
        poll();
    }

    // Apply chat result (from sync response or background task result) to the UI
    function applyChatResult(data, messageInput, messagesArea) {
        if (data.conversation_id && data.conversation_id !== currentConversationId) {
            currentConversationId = data.conversation_id;
        }
        if (data.user_message_id) {
            const userMsg = messagesArea.querySelector('.message.user:last-child');
            if (userMsg && !loadedMessageIds.has(data.user_message_id)) {
                userMsg.setAttribute('data-message-id', data.user_message_id);
                loadedMessageIds.add(data.user_message_id);
                totalMessages++;
            }
        }
        if (data.response) {
            if (data.message_id && loadedMessageIds.has(data.message_id)) {
                updateMessageCount();
                scrollToBottom();
                    } else {
                        const agentMessageDiv = document.createElement('div');
                        agentMessageDiv.className = 'message agent';
                        const { cleanedContent, reasoning } = extractReasoning(data.response);
                        if (data.tool_calls && data.tool_calls.length > 0) {
                            const toolCallsContainer = document.createElement('div');
                            toolCallsContainer.className = 'live-tool-calls tool-calls-inline';
                            renderLiveToolCalls(toolCallsContainer, data.tool_calls);
                            agentMessageDiv.appendChild(toolCallsContainer);
                        }
                        const agentContentDiv = document.createElement('div');
                        agentContentDiv.className = 'message-content';
                        agentContentDiv.innerHTML = renderMarkdown(cleanedContent);
                        agentMessageDiv.appendChild(agentContentDiv);
                        if (reasoning) {
                    const reasoningSection = createReasoningSection(reasoning, data.message_id || 'temp-' + Date.now());
                    if (reasoningSection) agentMessageDiv.appendChild(reasoningSection);
                }
                if (data.generated_files && data.generated_files.length > 0) {
                    const filesContainer = document.createElement('div');
                    filesContainer.className = 'message-files';
                    filesContainer.style.marginTop = '10px';
                    filesContainer.style.display = 'flex';
                    filesContainer.style.flexWrap = 'wrap';
                    filesContainer.style.gap = '8px';
                    data.generated_files.forEach(file => {
                        const fileUrl = ensureAbsoluteUrl(file.file_url);
                        const fileElement = document.createElement('div');
                        fileElement.style.display = 'inline-block';
                        if (file.file_type && file.file_type.startsWith('image/')) {
                            const img = document.createElement('img');
                            img.src = fileUrl;
                            img.alt = file.file_name;
                            img.style.maxWidth = '300px';
                            img.style.maxHeight = '300px';
                            img.style.borderRadius = '8px';
                            img.style.cursor = 'pointer';
                            img.onclick = () => window.open(fileUrl, '_blank');
                            fileElement.appendChild(img);
                        } else {
                            const fileLink = document.createElement('a');
                            fileLink.href = fileUrl;
                            fileLink.download = file.file_name;
                            fileLink.target = '_blank';
                            fileLink.className = 'file-item';
                            fileLink.innerHTML = `<i class="bi bi-file-earmark"></i> ${file.file_name} <i class="bi bi-download" style="margin-left: 4px;"></i>`;
                            fileElement.appendChild(fileLink);
                        }
                        filesContainer.appendChild(fileElement);
                    });
                    agentMessageDiv.appendChild(filesContainer);
                }
                if (data.message_id) {
                    agentMessageDiv.setAttribute('data-message-id', data.message_id);
                    loadedMessageIds.add(data.message_id);
                    totalMessages++;
                }
                if (data.message_id) {
                    const feedbackDiv = document.createElement('div');
                    feedbackDiv.className = 'message-feedback';
                    feedbackDiv.innerHTML = `
                        <button class="feedback-btn positive" onclick="submitFeedback(${data.message_id}, 'positive', this)" data-message-id="${data.message_id}">
                            <i class="bi bi-hand-thumbs-up"></i> Helpful
                        </button>
                        <button class="feedback-btn negative" onclick="submitFeedback(${data.message_id}, 'negative', this)" data-message-id="${data.message_id}">
                            <i class="bi bi-hand-thumbs-down"></i> Not Helpful
                        </button>
                    `;
                    agentMessageDiv.appendChild(feedbackDiv);
                }
                messagesArea.appendChild(agentMessageDiv);
                setTimeout(() => highlightCodeBlocks(agentMessageDiv), 50);
                updateMessageCount();
                if (toolCallsPanelVisible) loadToolCalls();
                scrollToBottom();
            }
        }
        clearFiles();
        updateConversationList();
        messageInput.disabled = false;
        document.getElementById('sendButton').disabled = false;
        messageInput.focus();
    }

    // Send message
    function sendMessage(event) {
        event.preventDefault();
        
        // Prevent sending messages in view mode
        if (isViewMode) {
            showAlert('You cannot send messages in read-only view mode', 'warning');
            return;
        }
        
        if (!currentAgentId) {
            showAlert('Please select an agent first', 'warning');
            return;
        }
        
        const messageInput = document.getElementById('messageInput');
        const originalMessage = messageInput.value.trim();
        
        // If files are uploaded, automatically add file names to the message
        let message = originalMessage;
        if (uploadedFiles.length > 0) {
            const fileNames = uploadedFiles.map(f => f.file_name).join(', ');
            if (message) {
                // If user has typed a message, append file names
                message = `${message}\n\nAttached files: ${fileNames}`;
            } else {
                // If no message, just mention the files
                message = `Attached files: ${fileNames}`;
            }
        }
        
        // Allow sending even if only files are attached (no text message)
        if (!message && uploadedFiles.length === 0) {
            return;
        }
        
        // Disable input while sending
        messageInput.disabled = true;
        document.getElementById('sendButton').disabled = true;
        
        // Show typing indicator
        document.getElementById('typingIndicator').classList.add('active');
        
        // Add user message to chat (show original message + file names if files are attached)
        const messagesArea = document.getElementById('chatMessages');
        if (messagesArea.querySelector('.empty-state')) {
            messagesArea.innerHTML = '';
        }
        
        const userMessageDiv = document.createElement('div');
        userMessageDiv.className = 'message user';
        // Display original message + file names if files are attached
        let displayMessage = originalMessage;
        if (uploadedFiles.length > 0) {
            const fileNames = uploadedFiles.map(f => f.file_name).join(', ');
            if (displayMessage) {
                displayMessage = `${displayMessage}\n\n📎 Attached files: ${fileNames}`;
            } else {
                displayMessage = `📎 Attached files: ${fileNames}`;
            }
        }
        // Render markdown for user message
        const userContentDiv = document.createElement('div');
        userContentDiv.className = 'message-content';
        userContentDiv.innerHTML = renderMarkdown(displayMessage);
        userMessageDiv.appendChild(userContentDiv);
        messagesArea.appendChild(userMessageDiv);
        scrollToBottom();
        
        // Clear input and reset textarea height
        messageInput.value = '';
        messageInput.style.height = 'auto';
        
        // Send to server with file IDs (message includes file names)
        fetch('/api/chat/send/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({
                agent_id: currentAgentId,
                message: message,
                conversation_id: currentConversationId,
                file_ids: uploadedFiles.map(f => f.file_id)
            })
        })
        .then(response => {
            return response.json().then(data => ({ response, data }));
        })
        .then(({ response, data }) => {
            // 202 = processing in background; add placeholder and poll (live tool calls)
            if (response.status === 202 && data.success && data.background && data.task_id) {
                if (data.conversation_id && data.conversation_id !== currentConversationId) {
                    currentConversationId = data.conversation_id;
                }
                var placeholder = document.createElement('div');
                placeholder.className = 'message agent pending';
                placeholder.setAttribute('data-task-id', data.task_id);
                placeholder.innerHTML = '<div class="live-tool-calls"></div><div class="message-content">Thinking...</div>';
                messagesArea.appendChild(placeholder);
                scrollToBottom();
                pollChatTask(data.task_id, messageInput, messagesArea);
                return;
            }
            
            document.getElementById('typingIndicator').classList.remove('active');
            
            if (data.success) {
                // Update conversation ID if new conversation was created
                if (data.conversation_id && data.conversation_id !== currentConversationId) {
                    currentConversationId = data.conversation_id;
                    // Reload conversation to get proper message IDs and show all messages
                    loadConversation(data.conversation_id, currentAgentId, null);
                    // Re-enable input after conversation loads
                    messageInput.disabled = false;
                    document.getElementById('sendButton').disabled = false;
                    messageInput.focus();
                    return;
                }
                
                // If this is an existing conversation, ensure we're showing the latest messages
                // by scrolling to bottom after adding new messages
                
                // Update message IDs and counts
                if (data.user_message_id) {
                    const userMsg = messagesArea.querySelector('.message.user:last-child');
                    if (userMsg && !loadedMessageIds.has(data.user_message_id)) {
                        userMsg.setAttribute('data-message-id', data.user_message_id);
                        loadedMessageIds.add(data.user_message_id);
                        totalMessages++;
                    }
                }
                
                // Add agent response
                if (data.response) {
                    // Check if message already exists to prevent duplicates
                    if (data.message_id && loadedMessageIds.has(data.message_id)) {
                        // Message already exists, skip adding but still update counts
                        updateMessageCount();
                        scrollToBottom();
                    } else {
                        const agentMessageDiv = document.createElement('div');
                        agentMessageDiv.className = 'message agent';
                        
                        // Extract reasoning from response
                        const { cleanedContent, reasoning } = extractReasoning(data.response);
                        
                        const agentContentDiv = document.createElement('div');
                        agentContentDiv.className = 'message-content';
                        agentContentDiv.innerHTML = renderMarkdown(cleanedContent);
                        agentMessageDiv.appendChild(agentContentDiv);
                        
                        // Add reasoning section if present
                        if (reasoning) {
                            const reasoningSection = createReasoningSection(reasoning, data.message_id || 'temp-' + Date.now());
                            if (reasoningSection) {
                                agentMessageDiv.appendChild(reasoningSection);
                            }
                        }
                        
                        // Display generated files if any
                        if (data.generated_files && data.generated_files.length > 0) {
                            const filesContainer = document.createElement('div');
                            filesContainer.className = 'message-files';
                            filesContainer.style.marginTop = '10px';
                            filesContainer.style.display = 'flex';
                            filesContainer.style.flexWrap = 'wrap';
                            filesContainer.style.gap = '8px';
                            
                            data.generated_files.forEach(file => {
                                const fileUrl = ensureAbsoluteUrl(file.file_url);
                                const fileElement = document.createElement('div');
                                fileElement.style.display = 'inline-block';
                                
                                // Check if it's an image
                                if (file.file_type && file.file_type.startsWith('image/')) {
                                    const img = document.createElement('img');
                                    img.src = fileUrl;
                                    img.alt = file.file_name;
                                    img.style.maxWidth = '300px';
                                    img.style.maxHeight = '300px';
                                    img.style.borderRadius = '8px';
                                    img.style.cursor = 'pointer';
                                    img.onclick = () => window.open(fileUrl, '_blank');
                                    img.title = `Click to view full size: ${file.file_name}`;
                                    fileElement.appendChild(img);
                                } else {
                                    // For non-image files, show a download link
                                    const fileLink = document.createElement('a');
                                    fileLink.href = fileUrl;
                                    fileLink.download = file.file_name;  // Suggest filename for download
                                    fileLink.target = '_blank';
                                    fileLink.className = 'file-item';
                                    fileLink.style.textDecoration = 'none';
                                    fileLink.style.cursor = 'pointer';
                                    fileLink.title = `Download ${file.file_name}`;
                                    fileLink.innerHTML = `<i class="bi bi-file-earmark"></i> ${file.file_name} <i class="bi bi-download" style="margin-left: 4px;"></i>`;
                                    
                                    // Force download by adding click handler as fallback
                                    fileLink.onclick = function(e) {
                                        // Try programmatic download if download attribute doesn't work
                                        // This handles cases where browser blocks download attribute
                                        setTimeout(() => {
                                            // Check if download started, if not, try programmatic download
                                            fetch(fileUrl, {
                                                method: 'HEAD',
                                                credentials: 'include'
                                            })
                                            .then(response => {
                                                if (response.ok) {
                                                    // File exists, try full download
                                                    return fetch(fileUrl, {
                                                        method: 'GET',
                                                        credentials: 'include'
                                                    });
                                                }
                                                throw new Error('File not accessible');
                                            })
                                            .then(response => response.blob())
                                            .then(blob => {
                                                const url = window.URL.createObjectURL(blob);
                                                const a = document.createElement('a');
                                                a.href = url;
                                                a.download = file.file_name;
                                                document.body.appendChild(a);
                                                a.click();
                                                window.URL.revokeObjectURL(url);
                                                document.body.removeChild(a);
                                            })
                                            .catch(error => {
                                                // Silently fail - let browser handle it normally
                                                console.debug('Programmatic download not needed:', error);
                                            });
                                        }, 100);
                                    };
                                    
                                    fileElement.appendChild(fileLink);
                                }
                                
                                filesContainer.appendChild(fileElement);
                            });
                            
                            agentMessageDiv.appendChild(filesContainer);
                        }
                        
                        if (data.message_id) {
                            agentMessageDiv.setAttribute('data-message-id', data.message_id);
                            loadedMessageIds.add(data.message_id);
                            totalMessages++;
                        }
                        
                        // Add feedback buttons
                        if (data.message_id) {
                            const feedbackDiv = document.createElement('div');
                            feedbackDiv.className = 'message-feedback';
                            feedbackDiv.innerHTML = `
                                <button class="feedback-btn positive" onclick="submitFeedback(${data.message_id}, 'positive', this)" data-message-id="${data.message_id}">
                                    <i class="bi bi-hand-thumbs-up"></i> Helpful
                                </button>
                                <button class="feedback-btn negative" onclick="submitFeedback(${data.message_id}, 'negative', this)" data-message-id="${data.message_id}">
                                    <i class="bi bi-hand-thumbs-down"></i> Not Helpful
                                </button>
                            `;
                            agentMessageDiv.appendChild(feedbackDiv);
                        }
                        
                        messagesArea.appendChild(agentMessageDiv);
                        // Highlight code blocks in the new message
                        setTimeout(() => highlightCodeBlocks(agentMessageDiv), 50);
                        updateMessageCount();
                        
                        // Reload tool calls if panel is visible
                        if (toolCallsPanelVisible) {
                            loadToolCalls();
                        }
                        
                        scrollToBottom();
                    }
                }
                
                // Clear uploaded files after successful send
                clearFiles();
                
                // Update conversation list
                updateConversationList();
            } else {
                showAlert(data.error || 'Failed to send message', 'danger');
            }
            
            // Re-enable input (for both success and error cases)
            messageInput.disabled = false;
            document.getElementById('sendButton').disabled = false;
            messageInput.focus();
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('typingIndicator').classList.remove('active');
            showAlert('An error occurred while sending the message', 'danger');
            
            // Re-enable input
            messageInput.disabled = false;
            document.getElementById('sendButton').disabled = false;
            messageInput.focus();
        });
    }
    
    // Track displayed conversation IDs to prevent duplicates
    let displayedConversationIds = new Set();
    let isUpdatingConversationList = false;  // Prevent concurrent updates
    
    // Initialize displayed conversation IDs from server-side rendered items
    function initializeDisplayedConversationIds() {
        const listContainer = document.getElementById('conversationsList');
        if (listContainer) {
            const existingItems = listContainer.querySelectorAll('.conversation-item[data-conversation-id]');
            existingItems.forEach(item => {
                const convId = item.getAttribute('data-conversation-id');
                if (convId) {
                    displayedConversationIds.add(convId);
                }
            });
        }
    }
    
    // Initialize on page load
    initializeDisplayedConversationIds();
    if (window.CHAT_CONFIG && window.CHAT_CONFIG.loadConversationsViaApi) {
        // Conversations skipped on server for fast load; fetch via API
        setTimeout(function() { if (typeof updateConversationList === 'function') updateConversationList(); }, 100);
    }
    
    // Update conversation list
    function updateConversationList() {
        // Prevent concurrent updates
        if (isUpdatingConversationList) {
            return;
        }
        
        isUpdatingConversationList = true;
        
        // Build API URL with optional agent_id filter
        let apiUrl = '/api/chat/conversations/';
        const selectedAgentId = document.getElementById('agentSelect')?.value;
        if (selectedAgentId) {
            apiUrl += `?agent_id=${selectedAgentId}`;
        }
        
        fetch(apiUrl)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const listContainer = document.getElementById('conversationsList');
                    
                    // Deduplicate conversations by ID (in case API returns duplicates)
                    const uniqueConversations = [];
                    const seenIds = new Set();
                    
                    data.conversations.forEach(conv => {
                        const convId = conv.id;
                        if (!seenIds.has(convId)) {
                            seenIds.add(convId);
                            uniqueConversations.push(conv);
                        }
                    });
                    
                    // Sort by updated_at (most recent first) - backend already does this but ensure client-side order
                    uniqueConversations.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
                    
                    // Check for duplicates before clearing
                    const existingIds = new Set();
                    const existingItems = listContainer.querySelectorAll('.conversation-item[data-conversation-id]');
                    existingItems.forEach(item => {
                        const convId = item.getAttribute('data-conversation-id');
                        if (convId) {
                            existingIds.add(convId);
                        }
                    });
                    
                    // Only update if there are changes or if list is empty
                    const newIds = new Set(uniqueConversations.map(c => c.id));
                    const hasChanges = existingIds.size !== newIds.size || 
                                      ![...existingIds].every(id => newIds.has(id));
                    
                    if (hasChanges || existingIds.size === 0) {
                        // Clear existing list and reset tracking
                        listContainer.innerHTML = '';
                        displayedConversationIds.clear();
                        
                        if (uniqueConversations.length === 0) {
                            listContainer.innerHTML = '<div class="empty-state"><i class="bi bi-chat-dots" style="font-size: 3rem;"></i><p class="mt-3">No conversations yet</p></div>';
                        } else {
                            // Add conversations to DOM
                            uniqueConversations.forEach(conv => {
                                const convId = conv.id;
                                const agentId = conv.agent_id || conv.agentId;
                                displayedConversationIds.add(convId);
                                
                                const item = document.createElement('div');
                                item.className = 'conversation-item';
                                item.setAttribute('data-conversation-id', convId);
                                item.setAttribute('data-agent-id', agentId);
                                item.setAttribute('data-agent-name', conv.agent_name || '');
                                
                                if (convId === currentConversationId) {
                                    item.classList.add('active');
                                }
                                item.onclick = () => loadConversation(convId, agentId, item);
                                
                                const preview = conv.last_message ? conv.last_message.substring(0, 50) + '...' : 'No messages';
                                const date = new Date(conv.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
                                const safeAgentName = escapeHtml(conv.agent_name || '');
                                const safePreview = escapeHtml(preview);
                                const deleteBtnHtml = isViewMode ? '' : `
                                    <button type="button" class="conversation-delete-btn"
                                            title="Delete conversation"
                                            aria-label="Delete conversation"
                                            onclick="event.stopPropagation(); deleteConversation('${convId}', this);">
                                        <i class="bi bi-trash3"></i>
                                    </button>
                                `;
                                item.innerHTML = `
                                    <div class="conversation-row">
                                        <div class="conversation-body">
                                            <div class="fw-bold">${safeAgentName}</div>
                                            <div class="conversation-preview">${safePreview}</div>
                                            <div class="conversation-preview"><small>${date}</small></div>
                                        </div>
                                        ${deleteBtnHtml}
                                    </div>
                                `;
                                listContainer.appendChild(item);
                            });
                        }
                    }
                    if (typeof filterConversationsBySearch === 'function') {
                        try { filterConversationsBySearch(); } catch (_) {}
                    }
                }
                isUpdatingConversationList = false;
            })
            .catch(error => {
                console.error('Error updating conversation list:', error);
                isUpdatingConversationList = false;
            });
    }
    
    // Scroll to bottom
    function scrollToBottom() {
        const messagesArea = document.getElementById('chatMessages');
        if (messagesArea) {
            // Use requestAnimationFrame to ensure DOM is updated
            requestAnimationFrame(() => {
                messagesArea.scrollTop = messagesArea.scrollHeight;
            });
        }
    }
    
    // Auto-select agent from URL parameter (only if not in view mode)
    if (!isViewMode) {
        const preselectedAgentId = window.CHAT_CONFIG && window.CHAT_CONFIG.selectedAgentId;
        if (preselectedAgentId) {
            // Agent was selected from dashboard, auto-select it
            // Use setTimeout to ensure DOM and onAgentChange function are ready
            setTimeout(function() {
                const agentSelect = document.getElementById('agentSelect');
                if (agentSelect) {
                    agentSelect.value = preselectedAgentId;
                    if (typeof onAgentChange === 'function') {
                        onAgentChange();
                    }
                }
            }, 150);
        }
        
        // Auto-focus input when agent is selected (only if not in view mode)
        const agentSelect = document.getElementById('agentSelect');
        if (agentSelect) {
            agentSelect.addEventListener('change', function() {
                if (this.value) {
                    setTimeout(() => {
                        const messageInput = document.getElementById('messageInput');
                        if (messageInput) messageInput.focus();
                    }, 100);
                }
            });
        }
        
        // Allow Enter to send (Shift+Enter for new line)
        const messageInput = document.getElementById('messageInput');
        if (messageInput) {
            messageInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    const chatForm = document.getElementById('chatForm');
                    if (chatForm) chatForm.dispatchEvent(new Event('submit'));
                }
            });
            // Auto-resize textarea as user types
            messageInput.addEventListener('input', function autoResize() {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 12 * 16) + 'px'; /* max 12rem */
            });
        }
    }
    
    // Handle file selection
    function handleFileSelect(event) {
        const files = Array.from(event.target.files);
        // Files selected
        
        if (!currentAgentId) {
            showAlert('Please select an agent first', 'warning');
            event.target.value = ''; // Clear selection
            return;
        }
        
        if (files.length === 0) {
            return;
        }
        
        files.forEach(file => {
            // Uploading file
            uploadFile(file);
        });
        
        // Reset input
        event.target.value = '';
    }
    
    // Upload file
    function uploadFile(file) {
        // Starting file upload
        
        // Validate file size (client-side check)
        const maxSize = 100 * 1024 * 1024; // 100MB
        if (file.size > maxSize) {
            showAlert(`File "${file.name}" exceeds 100MB limit`, 'danger');
            return;
        }
        
        // Show loading state
        const fileItem = document.createElement('span');
        fileItem.className = 'file-item';
        fileItem.id = `file-uploading-${Date.now()}`;
        fileItem.innerHTML = `<span class="file-name">${file.name} (uploading...)</span>`;
        const uploadedFilesContainer = document.getElementById('uploadedFiles');
        const clearFilesBtn = document.getElementById('clearFilesBtn');
        if (uploadedFilesContainer) {
            uploadedFilesContainer.appendChild(fileItem);
            if (clearFilesBtn) {
                clearFilesBtn.style.display = 'inline-block';
            }
        }
        
        const formData = new FormData();
        formData.append('file', file);
        formData.append('agent_id', currentAgentId);
        
        // Get CSRF token
        const csrftoken = getCookie('csrftoken');
        // CSRF token check
        
        // Don't set Content-Type header - browser will set it automatically with boundary for multipart/form-data
        const headers = {};
        if (csrftoken) {
            headers['X-CSRFToken'] = csrftoken;
        }
        
        fetch('/api/chat/upload-file/', {
            method: 'POST',
            headers: headers,
            body: formData,
            credentials: 'same-origin'
        })
        .then(response => {
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                return response.text().then(text => {
                    console.error('Non-JSON response:', text);
                    throw new Error(`Server returned non-JSON response: ${response.status}`);
                });
            }
            
            if (!response.ok) {
                return response.json().then(err => {
                    throw new Error(err.error || `HTTP error! status: ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            // Remove loading indicator
            fileItem.remove();
            
            if (data.success) {
                uploadedFiles.push({
                    file_id: data.file_id,
                    file_name: data.file_name,
                    file_size: data.file_size
                });
                updateFileDisplay();
                showAlert(`File "${data.file_name}" uploaded successfully`, 'success');
            } else {
                showAlert(data.error || 'Failed to upload file', 'danger');
            }
        })
        .catch(error => {
            console.error('File upload error:', error);
            if (fileItem && fileItem.parentNode) {
                fileItem.remove();
            }
            const errorMsg = error.message || 'An error occurred while uploading the file';
            console.error('Upload error details:', errorMsg);
            showAlert(`Failed to upload "${file.name}": ${errorMsg}`, 'danger');
        });
    }
    
    // Update file display
    function updateFileDisplay() {
        const container = document.getElementById('uploadedFiles');
        const clearBtn = document.getElementById('clearFilesBtn');
        const uploadArea = document.getElementById('fileUploadArea');
        
        // Check if elements exist before accessing
        if (!container) {
            // uploadedFiles container not found
            return;
        }
        
        if (uploadedFiles.length === 0) {
            container.innerHTML = '';
            if (clearBtn) {
                clearBtn.style.display = 'none';
            }
            if (uploadArea) uploadArea.style.display = 'none';
            return;
        }
        
        if (uploadArea) uploadArea.style.display = 'block';
        if (clearBtn) {
            clearBtn.style.display = 'inline-block';
        }
        container.innerHTML = uploadedFiles.map((file, index) => `
            <span class="file-item">
                <span class="file-name">${escapeHtml(file.file_name)}</span>
                <span class="file-remove" onclick="removeFile(${index})" title="Remove">×</span>
            </span>
        `).join('');
    }
    
    // Remove file
    function removeFile(index) {
        uploadedFiles.splice(index, 1);
        updateFileDisplay();
    }
    
    // Clear all files
    function clearFiles() {
        uploadedFiles = [];
        updateFileDisplay();
    }
    
    // Handle attach file button click
    function attachFileClick() {
        if (!currentAgentId) {
            showAlert('Please select an agent first', 'warning');
            return;
        }
        const fileInput = document.getElementById('fileInput');
        if (fileInput) {
            fileInput.click();
        } else {
            console.error('File input element not found');
            showAlert('File upload not available', 'danger');
        }
    }
    
    // Submit feedback
    function submitFeedback(messageId, feedbackType, buttonElement) {
        if (!currentConversationId) {
            showAlert('No active conversation', 'warning');
            return;
        }
        
        // Update button state
        const feedbackDiv = buttonElement.parentElement;
        feedbackDiv.querySelectorAll('.feedback-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        buttonElement.classList.add('active', feedbackType);
        
        fetch('/api/chat/feedback/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify({
                message_id: messageId,
                feedback_type: feedbackType,
                conversation_id: currentConversationId
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showAlert('Thank you for your feedback!', 'success');
            } else {
                showAlert(data.error || 'Failed to submit feedback', 'danger');
                // Revert button state
                buttonElement.classList.remove('active', feedbackType);
            }
        })
        .catch(error => {
            console.error('Feedback error:', error);
            showAlert('An error occurred while submitting feedback', 'danger');
            // Revert button state
            buttonElement.classList.remove('active', feedbackType);
        });
    }
    
    // Mobile sidebar toggle
    function toggleMobileSidebar() {
        const sidebar = document.getElementById('chatSidebar');
        const overlay = document.getElementById('mobileSidebarOverlay');
        const toggle = document.getElementById('mobileSidebarToggle');
        
        if (sidebar && overlay && toggle) {
            sidebar.classList.toggle('mobile-show');
            overlay.classList.toggle('show');
            
            // Update toggle button icon
            const icon = toggle.querySelector('i');
            if (sidebar.classList.contains('mobile-show')) {
                icon.className = 'bi bi-x-lg';
            } else {
                icon.className = 'bi bi-chat-left-text';
            }
        }
    }
    
    // Close mobile sidebar when clicking outside or selecting conversation
    function closeMobileSidebar() {
        const sidebar = document.getElementById('chatSidebar');
        const overlay = document.getElementById('mobileSidebarOverlay');
        const toggle = document.getElementById('mobileSidebarToggle');
        
        if (sidebar && overlay && toggle) {
            sidebar.classList.remove('mobile-show');
            overlay.classList.remove('show');
            const icon = toggle.querySelector('i');
            if (icon) {
                icon.className = 'bi bi-chat-left-text';
            }
        }
    }
    
    // Close sidebar when conversation is selected (mobile)
    const originalLoadConversation = window.loadConversation;
    if (originalLoadConversation) {
        window.loadConversation = function(conversationId, agentId, element) {
            closeMobileSidebar();
            return originalLoadConversation(conversationId, agentId, element);
        };
    }
    
    // Expose handlers referenced from inline HTML (onclick / onchange / onsubmit)
    const startNewChatImpl = startNewChat;
    window.toggleSidebar = toggleSidebar;
    window.toggleMobileSidebar = toggleMobileSidebar;
    window.startNewChat = function() {
        closeMobileSidebar();
        return startNewChatImpl();
    };
    window.onAgentChange = onAgentChange;
    window.sendMessage = sendMessage;
    window.filterConversationsBySearch = filterConversationsBySearch;
    window.clearConversationSearch = clearConversationSearch;
    window.loadOlderMessages = loadOlderMessages;
    window.downloadChatHistory = downloadChatHistory;
    window.handleFileSelect = handleFileSelect;
    window.attachFileClick = attachFileClick;
    window.toggleSpeechRecognition = toggleSpeechRecognition;

    // Handle window resize
    window.addEventListener('resize', function() {
        if (window.innerWidth >= 768) {
            // Desktop view - ensure sidebar is visible
            const sidebar = document.getElementById('chatSidebar');
            const overlay = document.getElementById('mobileSidebarOverlay');
            if (sidebar) sidebar.classList.remove('mobile-show');
            if (overlay) overlay.classList.remove('show');
        }
    });
