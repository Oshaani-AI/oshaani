"""Celery tasks for connector data synchronization."""
import logging
from celery import shared_task
from django.utils import timezone
from .models import Connector, ConnectorSync
from .jira_oauth import JIRAOAuthClient, ConfluenceOAuthClient
from .gitlab_oauth import GitLabOAuthClient
from .github_oauth import GitHubOAuthClient
from .google_oauth import GoogleOAuthClient
from .microsoft_oauth import MicrosoftOAuthClient
from .validator import ConnectorValidator
from agents_app.models import TrainingData

logger = logging.getLogger(__name__)

# Maximum training data items per connector sync
MAX_TRAINING_DATA_PER_SYNC = 100

# Maximum training data items per agent (must match views_dashboard.py)
MAX_TRAINING_DATA_PER_AGENT = 150


def _training_data_exists(agent, source, unique_id_key, unique_id_value):
    """
    Check if training data already exists for a given source and unique identifier.
    
    Args:
        agent: Agent instance
        source: Source identifier (e.g., 'jira_connector_1')
        unique_id_key: Key in content JSON that contains unique ID (e.g., 'key', 'id', 'slug')
        unique_id_value: Value of the unique identifier
    
    Returns:
        bool: True if training data exists, False otherwise
    """
    if not unique_id_value:
        return False
    
    # Query training data for this agent with matching source and unique ID
    # Using Django JSONField lookup syntax
    existing = TrainingData.objects.filter(
        agent=agent,
        data_type='text',
        content__source=source
    ).filter(**{f'content__{unique_id_key}': unique_id_value})
    
    return existing.exists()


@shared_task(bind=True, max_retries=3)
def sync_connector_data(self, sync_id):
    """Sync data from connector to agent training data."""
    try:
        sync = ConnectorSync.objects.get(id=sync_id)
        connector = sync.connector
        agent = sync.agent
        
        sync.status = 'running'
        sync.started_at = timezone.now()
        sync.save()
        
        # Check current training data count
        current_training_data_count = agent.training_data.count()
        remaining_slots = MAX_TRAINING_DATA_PER_AGENT - current_training_data_count
        
        if remaining_slots <= 0:
            logger.warning(f"Agent {agent.id} has reached maximum training data limit ({MAX_TRAINING_DATA_PER_AGENT}). Cannot sync more data.")
            sync.status = 'failed'
            sync.completed_at = timezone.now()
            sync.error_message = f"Maximum training data limit ({MAX_TRAINING_DATA_PER_AGENT}) reached for this agent. Please delete some training data before syncing more."
            sync.save()
            return
        
        # Limit items to sync based on remaining slots
        max_items_to_sync = min(MAX_TRAINING_DATA_PER_SYNC, remaining_slots)
        
        items_synced = 0
        items_failed = 0
        error_messages = []
        
        if connector.connector_type == 'jira':
            # Sync JIRA issues
            client = JIRAOAuthClient(connector)
            
            # Get JQL from configuration or use default (closed/completed tasks only)
            # Using statusCategory = Done is more universal than specific status names
            jql = connector.configuration.get('jql', 'statusCategory = Done ORDER BY updated DESC')
            max_results = min(connector.configuration.get('max_results', 100), MAX_TRAINING_DATA_PER_SYNC)
            
            try:
                issues = client.fetch_issues(jql=jql, max_results=max_results)
                
                for issue in issues:
                    # Check if we've reached the maximum limit (both per sync and per agent)
                    if items_synced >= max_items_to_sync:
                        logger.info(f"Reached maximum limit of {max_items_to_sync} training data items for connector {connector.id} (agent limit: {MAX_TRAINING_DATA_PER_AGENT})")
                        break
                    
                    # Double-check current count hasn't exceeded limit (in case of concurrent operations)
                    if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                        logger.warning(f"Agent {agent.id} training data limit reached during sync. Stopping.")
                        break
                    
                    try:
                        issue_key = issue.get('key')
                        source = f'jira_connector_{connector.id}'
                        
                        # Check if this issue already exists
                        if _training_data_exists(agent, source, 'key', issue_key):
                            logger.debug(f"JIRA issue {issue_key} already synced, skipping")
                            continue
                        
                        # Format issue as training data
                        content = {
                            'key': issue_key,
                            'summary': issue.get('summary', ''),
                            'description': issue.get('description', ''),
                            'status': issue.get('status', ''),
                            'priority': issue.get('priority', ''),
                            'assignee': issue.get('assignee', ''),
                            'reporter': issue.get('reporter', ''),
                            'created': issue.get('created', ''),
                            'updated': issue.get('updated', ''),
                            'comments': issue.get('comments', []),
                            'source': source,  # Store source in content
                        }
                        
                        # Final check before creating - ensure we haven't exceeded the limit
                        if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                            logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                            break
                        
                        # Create training data entry
                        TrainingData.objects.create(
                            agent=agent,
                            data_type='text',
                            content=content,
                        )
                        
                        items_synced += 1
                        
                    except Exception as e:
                        logger.error(f"Error syncing JIRA issue {issue.get('key')}: {str(e)}", exc_info=True)
                        items_failed += 1
                        error_messages.append(f"Issue {issue.get('key')}: {str(e)}")
                
            except Exception as e:
                logger.error(f"Error fetching JIRA issues: {str(e)}", exc_info=True)
                raise
        
        elif connector.connector_type == 'confluence':
            # Sync Confluence pages
            client = ConfluenceOAuthClient(connector)
            
            # Get space key from configuration or fetch all
            space_key = connector.configuration.get('space_key')
            limit = min(connector.configuration.get('limit', 100), max_items_to_sync)
            
            try:
                pages = client.fetch_pages(space_key=space_key, limit=limit)
                
                for page in pages:
                    # Check if we've reached the maximum limit (both per sync and per agent)
                    if items_synced >= max_items_to_sync:
                        logger.info(f"Reached maximum limit of {max_items_to_sync} training data items for connector {connector.id} (agent limit: {MAX_TRAINING_DATA_PER_AGENT})")
                        break
                    
                    # Double-check current count hasn't exceeded limit (in case of concurrent operations)
                    if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                        logger.warning(f"Agent {agent.id} training data limit reached during sync. Stopping.")
                        break
                    
                    try:
                        page_id = page.get('id')
                        source = f'confluence_connector_{connector.id}'
                        
                        # Check if this page already exists
                        if _training_data_exists(agent, source, 'id', page_id):
                            logger.debug(f"Confluence page {page_id} already synced, skipping")
                            continue
                        
                        # Format page as training data
                        content = {
                            'id': page_id,
                            'title': page.get('title', ''),
                            'body': page.get('body', ''),
                            'space_key': page.get('space_key', ''),
                            'space_name': page.get('space_name', ''),
                            'version': page.get('version', 1),
                            'created': page.get('created', ''),
                            'updated': page.get('updated', ''),
                            'source': source,  # Store source in content
                        }
                        
                        # Final check before creating - ensure we haven't exceeded the limit
                        if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                            logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                            break
                        
                        # Create training data entry
                        TrainingData.objects.create(
                            agent=agent,
                            data_type='text',
                            content=content,
                        )
                        
                        items_synced += 1
                        
                    except Exception as e:
                        logger.error(f"Error syncing Confluence page {page.get('id')}: {str(e)}", exc_info=True)
                        items_failed += 1
                        error_messages.append(f"Page {page.get('id')}: {str(e)}")
                
            except Exception as e:
                logger.error(f"Error fetching Confluence pages: {str(e)}", exc_info=True)
                raise
        
        elif connector.connector_type == 'gitlab':
            # Sync GitLab data
            client = GitLabOAuthClient(connector)
            
            # Get sync configuration
            sync_type = connector.configuration.get('sync_type', 'issues')  # issues, merge_requests, wiki, files
            project_id = connector.configuration.get('project_id')
            limit = min(connector.configuration.get('limit', 100), max_items_to_sync)
            
            try:
                if sync_type == 'issues':
                    # Sync GitLab issues
                    state = connector.configuration.get('issue_state', 'opened')
                    issues = client.fetch_issues(project_id=project_id, state=state, limit=limit)
                    
                    for issue in issues:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            issue_id = issue.get('id')
                            source = f'gitlab_connector_{connector.id}'
                            
                            # Check if this issue already exists
                            if _training_data_exists(agent, source, 'id', issue_id):
                                logger.debug(f"GitLab issue {issue_id} already synced, skipping")
                                continue
                            
                            content = {
                                'id': issue_id,
                                'iid': issue.get('iid'),
                                'title': issue.get('title', ''),
                                'description': issue.get('description', ''),
                                'state': issue.get('state', ''),
                                'labels': issue.get('labels', []),
                                'author': issue.get('author', ''),
                                'assignee': issue.get('assignee', ''),
                                'created_at': issue.get('created_at', ''),
                                'updated_at': issue.get('updated_at', ''),
                                'web_url': issue.get('web_url', ''),
                                'project_id': issue.get('project_id'),
                                'source': source,  # Store source in content
                            }
                            
                            # Final check before creating - ensure we haven't exceeded the limit
                            if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                                logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                                break
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing GitLab issue {issue.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Issue {issue.get('id')}: {str(e)}")
                
                elif sync_type == 'merge_requests':
                    # Sync GitLab merge requests
                    state = connector.configuration.get('mr_state', 'opened')
                    mrs = client.fetch_merge_requests(project_id=project_id, state=state, limit=limit)
                    
                    for mr in mrs:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            content = {
                                'id': mr.get('id'),
                                'iid': mr.get('iid'),
                                'title': mr.get('title', ''),
                                'description': mr.get('description', ''),
                                'state': mr.get('state', ''),
                                'source_branch': mr.get('source_branch', ''),
                                'target_branch': mr.get('target_branch', ''),
                                'author': mr.get('author', ''),
                                'assignee': mr.get('assignee', ''),
                                'created_at': mr.get('created_at', ''),
                                'updated_at': mr.get('updated_at', ''),
                                'web_url': mr.get('web_url', ''),
                                'project_id': mr.get('project_id'),
                                'source': f'gitlab_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing GitLab MR {mr.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"MR {mr.get('id')}: {str(e)}")
                
                elif sync_type == 'wiki':
                    # Sync GitLab wiki pages
                    if not project_id:
                        raise Exception("project_id is required for wiki sync")
                    
                    wiki_pages = client.fetch_wiki_pages(project_id=project_id)
                    
                    for page in wiki_pages:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            content = {
                                'slug': page.get('slug', ''),
                                'title': page.get('title', ''),
                                'content': page.get('content', ''),
                                'format': page.get('format', 'markdown'),
                                'created_at': page.get('created_at', ''),
                                'updated_at': page.get('updated_at', ''),
                                'project_id': project_id,
                                'source': f'gitlab_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing GitLab wiki page {page.get('slug')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Wiki {page.get('slug')}: {str(e)}")
                
                elif sync_type == 'files':
                    # Sync GitLab repository files
                    if not project_id:
                        raise Exception("project_id is required for file sync")
                    
                    file_paths = connector.configuration.get('file_paths', [])  # List of file paths to sync
                    ref = connector.configuration.get('ref', 'main')
                    
                    if not file_paths:
                        # If no specific files, fetch all files in root
                        files = client.fetch_repository_files(project_id=project_id, path='', ref=ref)
                        file_paths = [f.get('path') for f in files if f.get('type') == 'blob']
                    
                    for file_path in file_paths:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            file_content = client.fetch_file_content(project_id=project_id, file_path=file_path, ref=ref)
                            
                            content = {
                                'file_name': file_content.get('file_name', ''),
                                'file_path': file_content.get('file_path', ''),
                                'content': file_content.get('content', ''),
                                'size': file_content.get('size', 0),
                                'encoding': file_content.get('encoding', ''),
                                'ref': file_content.get('ref', ref),
                                'project_id': project_id,
                                'source': f'gitlab_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing GitLab file {file_path}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"File {file_path}: {str(e)}")
                
                else:
                    raise Exception(f"Unsupported GitLab sync type: {sync_type}")
                
            except Exception as e:
                logger.error(f"Error fetching GitLab data: {str(e)}", exc_info=True)
                raise
        
        elif connector.connector_type == 'github':
            # Sync GitHub data
            client = GitHubOAuthClient(connector)
            
            # Get sync configuration
            sync_type = connector.configuration.get('sync_type', 'repositories')  # repositories, issues
            limit = min(connector.configuration.get('limit', 100), max_items_to_sync)
            
            try:
                if sync_type == 'repositories':
                    # Sync GitHub repositories
                    visibility = connector.configuration.get('visibility')  # 'all', 'public', 'private'
                    repos = client.fetch_repositories(visibility=visibility)
                    
                    for repo in repos[:limit]:  # Limit results
                        # Check if we've reached the maximum limit
                        if items_synced >= max_items_to_sync:
                            logger.info(f"Reached maximum limit of {max_items_to_sync} training data items for connector {connector.id}")
                            break
                        
                        try:
                            repo_id = repo.get('id')
                            source = f'github_connector_{connector.id}'
                            
                            # Check if this repository already exists
                            if _training_data_exists(agent, source, 'id', repo_id):
                                logger.debug(f"GitHub repository {repo.get('full_name')} already synced, skipping")
                                continue
                            
                            content = {
                                'id': repo_id,
                                'name': repo.get('name', ''),
                                'full_name': repo.get('full_name', ''),
                                'description': repo.get('description', ''),
                                'url': repo.get('url', ''),
                                'language': repo.get('language', ''),
                                'stars': repo.get('stars', 0),
                                'forks': repo.get('forks', 0),
                                'private': repo.get('private', False),
                                'created_at': repo.get('created_at', ''),
                                'updated_at': repo.get('updated_at', ''),
                                'source': source,
                            }
                            
                            # Final check before creating
                            if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                                logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                                break
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing GitHub repository {repo.get('full_name')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Repository {repo.get('full_name')}: {str(e)}")
                
                elif sync_type == 'issues':
                    # Sync GitHub issues
                    owner = connector.configuration.get('owner')  # Repository owner
                    repo = connector.configuration.get('repo')  # Repository name
                    state = connector.configuration.get('issue_state', 'closed')  # 'open', 'closed', 'all'
                    
                    if not owner or not repo:
                        raise Exception("GitHub issues sync requires 'owner' and 'repo' in configuration")
                    
                    issues = client.fetch_issues(owner=owner, repo=repo, state=state, max_results=limit)
                    
                    for issue in issues:
                        # Check if we've reached the maximum limit
                        if items_synced >= max_items_to_sync:
                            logger.info(f"Reached maximum limit of {max_items_to_sync} training data items for connector {connector.id}")
                            break
                        
                        try:
                            issue_number = issue.get('number')
                            source = f'github_connector_{connector.id}'
                            
                            # Check if this issue already exists
                            if _training_data_exists(agent, source, 'number', issue_number):
                                logger.debug(f"GitHub issue #{issue_number} already synced, skipping")
                                continue
                            
                            content = {
                                'number': issue_number,
                                'title': issue.get('title', ''),
                                'body': issue.get('body', ''),
                                'state': issue.get('state', ''),
                                'labels': issue.get('labels', []),
                                'assignee': issue.get('assignee', ''),
                                'user': issue.get('user', ''),
                                'created_at': issue.get('created_at', ''),
                                'updated_at': issue.get('updated_at', ''),
                                'closed_at': issue.get('closed_at', ''),
                                'url': issue.get('url', ''),
                                'source': source,
                            }
                            
                            # Final check before creating
                            if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                                logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                                break
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing GitHub issue #{issue.get('number')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Issue #{issue.get('number')}: {str(e)}")
                
                else:
                    raise Exception(f"Unsupported GitHub sync type: {sync_type}")
                
            except Exception as e:
                logger.error(f"Error fetching GitHub data: {str(e)}", exc_info=True)
                raise
        
        elif connector.connector_type == 'google':
            # Sync Google data
            client = GoogleOAuthClient(connector)
            
            # Get sync configuration
            sync_type = connector.configuration.get('sync_type', 'drive')  # drive, docs, sheets, gmail
            limit = min(connector.configuration.get('limit', 100), max_items_to_sync)
            
            try:
                if sync_type == 'drive':
                    # Sync Google Drive files
                    folder_id = connector.configuration.get('folder_id')
                    files = client.fetch_drive_files(folder_id=folder_id, limit=limit)
                    
                    for file in files:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            # Fetch file content
                            file_content = client.fetch_drive_file_content(file['id'], file.get('mimeType'))
                            
                            content = {
                                'id': file_content.get('id'),
                                'name': file_content.get('name', ''),
                                'mimeType': file_content.get('mimeType', ''),
                                'content': file_content.get('content', ''),
                                'size': file_content.get('size', 0),
                                'createdTime': file_content.get('createdTime', ''),
                                'modifiedTime': file_content.get('modifiedTime', ''),
                                'source': f'google_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing Google Drive file {file.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"File {file.get('id')}: {str(e)}")
                
                elif sync_type == 'docs':
                    # Sync Google Docs
                    docs = client.fetch_docs(limit=limit)
                    
                    for doc in docs:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            doc_id = doc.get('id')
                            source = f'google_connector_{connector.id}'
                            
                            # Check if this doc already exists
                            if _training_data_exists(agent, source, 'id', doc_id):
                                logger.debug(f"Google Doc {doc_id} already synced, skipping")
                                continue
                            
                            content = {
                                'id': doc_id,
                                'name': doc.get('name', ''),
                                'content': doc.get('content', ''),
                                'createdTime': doc.get('createdTime', ''),
                                'modifiedTime': doc.get('modifiedTime', ''),
                                'webViewLink': doc.get('webViewLink', ''),
                                'source': source,  # Store source in content
                            }
                            
                            # Final check before creating - ensure we haven't exceeded the limit
                            if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                                logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                                break
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing Google Doc {doc.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Doc {doc.get('id')}: {str(e)}")
                
                elif sync_type == 'sheets':
                    # Sync Google Sheets
                    sheets = client.fetch_sheets(limit=limit)
                    
                    for sheet in sheets:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            sheet_id = sheet.get('id')
                            source = f'google_connector_{connector.id}'
                            
                            # Check if this sheet already exists
                            if _training_data_exists(agent, source, 'id', sheet_id):
                                logger.debug(f"Google Sheet {sheet_id} already synced, skipping")
                                continue
                            
                            content = {
                                'id': sheet_id,
                                'name': sheet.get('name', ''),
                                'content': sheet.get('content', ''),
                                'createdTime': sheet.get('createdTime', ''),
                                'modifiedTime': sheet.get('modifiedTime', ''),
                                'webViewLink': sheet.get('webViewLink', ''),
                                'source': source,  # Store source in content
                            }
                            
                            # Final check before creating - ensure we haven't exceeded the limit
                            if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                                logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                                break
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing Google Sheet {sheet.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Sheet {sheet.get('id')}: {str(e)}")
                
                elif sync_type == 'gmail':
                    # Sync Gmail messages
                    query = connector.configuration.get('query')
                    messages = client.fetch_gmail_messages(query=query, limit=limit)
                    
                    for msg in messages:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            content = {
                                'id': msg.get('id'),
                                'threadId': msg.get('threadId'),
                                'subject': msg.get('subject', ''),
                                'from': msg.get('from', ''),
                                'date': msg.get('date', ''),
                                'snippet': msg.get('snippet', ''),
                                'body': msg.get('body', ''),
                                'source': f'google_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing Gmail message {msg.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Message {msg.get('id')}: {str(e)}")
                
                else:
                    raise Exception(f"Unsupported Google sync type: {sync_type}")
                
            except Exception as e:
                logger.error(f"Error fetching Google data: {str(e)}", exc_info=True)
                raise
        
        elif connector.connector_type == 'microsoft':
            # Sync Microsoft data
            client = MicrosoftOAuthClient(connector)
            
            # Get sync configuration
            sync_type = connector.configuration.get('sync_type', 'onedrive')  # onedrive, sharepoint, outlook, teams
            limit = min(connector.configuration.get('limit', 100), max_items_to_sync)
            
            try:
                if sync_type == 'onedrive':
                    # Sync OneDrive files
                    folder_path = connector.configuration.get('folder_path')
                    files = client.fetch_onedrive_files(folder_path=folder_path, limit=limit)
                    
                    for file in files:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            # Fetch file content
                            file_content = client.fetch_onedrive_file_content(file['id'])
                            
                            content = {
                                'id': file_content.get('id'),
                                'name': file_content.get('name', ''),
                                'mimeType': file_content.get('mimeType', ''),
                                'content': file_content.get('content', ''),
                                'size': file_content.get('size', 0),
                                'createdDateTime': file_content.get('createdDateTime', ''),
                                'lastModifiedDateTime': file_content.get('lastModifiedDateTime', ''),
                                'webUrl': file_content.get('webUrl', ''),
                                'source': f'microsoft_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing OneDrive file {file.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"File {file.get('id')}: {str(e)}")
                
                elif sync_type == 'sharepoint':
                    # Sync SharePoint documents
                    site_id = connector.configuration.get('site_id')
                    docs = client.fetch_sharepoint_documents(site_id=site_id, limit=limit)
                    
                    for doc in docs:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            content = {
                                'id': doc.get('id'),
                                'name': doc.get('name', ''),
                                'mimeType': doc.get('mimeType', ''),
                                'size': doc.get('size', 0),
                                'createdDateTime': doc.get('createdDateTime', ''),
                                'lastModifiedDateTime': doc.get('lastModifiedDateTime', ''),
                                'webUrl': doc.get('webUrl', ''),
                                'source': f'microsoft_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing SharePoint document {doc.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Document {doc.get('id')}: {str(e)}")
                
                elif sync_type == 'outlook':
                    # Sync Outlook messages
                    folder_id = connector.configuration.get('folder_id')
                    messages = client.fetch_outlook_messages(folder_id=folder_id, limit=limit)
                    
                    for msg in messages:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            msg_id = msg.get('id')
                            source = f'microsoft_connector_{connector.id}'
                            
                            # Check if this message already exists
                            if _training_data_exists(agent, source, 'id', msg_id):
                                logger.debug(f"Outlook message {msg_id} already synced, skipping")
                                continue
                            
                            content = {
                                'id': msg_id,
                                'subject': msg.get('subject', ''),
                                'from': msg.get('from', ''),
                                'receivedDateTime': msg.get('receivedDateTime', ''),
                                'bodyPreview': msg.get('bodyPreview', ''),
                                'body': msg.get('body', ''),
                                'toRecipients': msg.get('toRecipients', []),
                                'source': source,  # Store source in content
                            }
                            
                            # Final check before creating - ensure we haven't exceeded the limit
                            if agent.training_data.count() >= MAX_TRAINING_DATA_PER_AGENT:
                                logger.warning(f"Agent {agent.id} training data limit reached. Stopping sync.")
                                break
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing Outlook message {msg.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Message {msg.get('id')}: {str(e)}")
                
                elif sync_type == 'teams':
                    # Sync Teams messages
                    team_id = connector.configuration.get('team_id')
                    channel_id = connector.configuration.get('channel_id')
                    messages = client.fetch_teams_messages(team_id=team_id, channel_id=channel_id, limit=limit)
                    
                    for msg in messages:
                        # Check if we've reached the maximum limit
                        if items_synced >= MAX_TRAINING_DATA_PER_SYNC:
                            logger.info(f"Reached maximum limit of {MAX_TRAINING_DATA_PER_SYNC} training data items for connector {connector.id}")
                            break
                        
                        try:
                            content = {
                                'id': msg.get('id'),
                                'createdDateTime': msg.get('createdDateTime', ''),
                                'body': msg.get('body', ''),
                                'from': msg.get('from', ''),
                                'source': f'microsoft_connector_{connector.id}',  # Store source in content
                            }
                            
                            TrainingData.objects.create(
                                agent=agent,
                                data_type='text',
                                content=content,
                            )
                            
                            items_synced += 1
                            
                        except Exception as e:
                            logger.error(f"Error syncing Teams message {msg.get('id')}: {str(e)}", exc_info=True)
                            items_failed += 1
                            error_messages.append(f"Message {msg.get('id')}: {str(e)}")
                
                else:
                    raise Exception(f"Unsupported Microsoft sync type: {sync_type}")
                
            except Exception as e:
                logger.error(f"Error fetching Microsoft data: {str(e)}", exc_info=True)
                raise
        
        else:
            raise Exception(f"Unsupported connector type: {connector.connector_type}")
        
        # Update sync status
        sync.status = 'completed'
        sync.items_synced = items_synced
        sync.items_failed = items_failed
        sync.completed_at = timezone.now()
        sync.result_data = {
            'items_synced': items_synced,
            'items_failed': items_failed,
            'errors': error_messages[:10],  # Limit to first 10 errors
        }
        if error_messages:
            sync.error_message = '\n'.join(error_messages[:5])  # Store first 5 errors
        sync.save()
        
        # Update agent training data count
        agent.training_data_count = agent.training_data.count()
        agent.save(update_fields=['training_data_count'])
        
        # Update connector last sync time
        connector.last_sync_at = timezone.now()
        connector.save(update_fields=['last_sync_at'])
        
        # Index training data for RAG if any items were synced
        if items_synced > 0:
            try:
                from agents_app.tasks import index_training_data_for_rag
                # Trigger indexing for all agent training data (RAG service will re-index everything)
                logger.info(f"[RAG Training] Triggering RAG indexing for agent {agent.id} after sync {sync_id} ({items_synced} items synced)")
                index_training_data_for_rag.delay(agent.id)
                logger.info(f"[RAG Training] RAG indexing task queued for agent {agent.id}")
            except Exception as e:
                logger.warning(f"[RAG Training] Failed to trigger RAG indexing after sync {sync_id}: {str(e)}", exc_info=True)
                # Don't fail the sync if indexing fails
        
        logger.info(f"Completed sync {sync_id}: {items_synced} items synced, {items_failed} failed")
        
    except ConnectorSync.DoesNotExist:
        logger.error(f"Sync {sync_id} not found")
    except Exception as e:
        logger.error(f"Error in sync task {sync_id}: {str(e)}", exc_info=True)
        
        # Update sync status to failed
        try:
            sync = ConnectorSync.objects.get(id=sync_id)
            sync.status = 'failed'
            sync.error_message = str(e)
            sync.completed_at = timezone.now()
            sync.save()
        except Exception:
            pass
        
        # Retry if not exceeded max retries
        raise self.retry(exc=e, countdown=60)


@shared_task
def validate_all_connectors():
    """Periodic task to validate all connectors and update their status."""
    try:
        connectors = Connector.objects.filter(status__in=['connected', 'error'])
        
        for connector in connectors:
            try:
                ConnectorValidator.validate_and_update_status(connector)
                logger.info(f"Validated connector {connector.id} ({connector.name}): {connector.status}")
            except Exception as e:
                logger.error(f"Error validating connector {connector.id}: {str(e)}", exc_info=True)
        
        logger.info(f"Completed validation of {connectors.count()} connectors")
        
    except Exception as e:
        logger.error(f"Error in validate_all_connectors task: {str(e)}", exc_info=True)
