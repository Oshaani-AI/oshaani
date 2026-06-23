"""Use case determination for AI models based on their characteristics."""
import re


def determine_model_use_cases(model_id, model_name, description, provider_name, input_modalities, output_modalities):
    """
    Determine best use cases for a model based on its characteristics.
    
    Returns a list of use case strings describing what the model is best suited for.
    """
    use_cases = []
    model_lower = model_name.lower() if model_name else ''
    desc_lower = description.lower() if description else ''
    model_id_lower = model_id.lower() if model_id else ''
    provider_lower = provider_name.lower() if provider_name else ''
    
    # Combine all text for analysis
    all_text = f"{model_lower} {desc_lower} {model_id_lower}".lower()
    
    # Claude models
    if 'claude' in all_text:
        if 'claude-3' in all_text or 'claude-3' in model_id_lower:
            if 'sonnet' in all_text:
                use_cases.extend([
                    'General-purpose conversations',
                    'Complex reasoning and analysis',
                    'Code generation and review',
                    'Content creation and editing',
                    'Data analysis and summarization',
                ])
            elif 'haiku' in all_text:
                use_cases.extend([
                    'Fast responses and simple queries',
                    'Content summarization',
                    'Quick data extraction',
                    'Simple Q&A',
                    'High-volume tasks',
                ])
            elif 'opus' in all_text:
                use_cases.extend([
                    'Complex problem solving',
                    'Advanced reasoning',
                    'Research and analysis',
                    'Creative writing',
                    'Technical documentation',
                ])
            else:
                use_cases.extend([
                    'General-purpose AI tasks',
                    'Conversational AI',
                    'Content generation',
                    'Question answering',
                ])
        else:
            # Claude 2
            use_cases.extend([
                'General-purpose conversations',
                'Text generation',
                'Question answering',
                'Content creation',
            ])
    
    # Llama models
    elif 'llama' in all_text:
        if 'chat' in all_text:
            use_cases.extend([
                'Conversational AI',
                'Chatbots and assistants',
                'Interactive Q&A',
            ])
        else:
            use_cases.extend([
                'Text generation',
                'Content creation',
                'Language understanding',
            ])
    
    # DeepSeek models
    elif 'deepseek' in all_text:
        if 'coder' in all_text or 'code' in all_text:
            use_cases.extend([
                'Code generation',
                'Programming assistance',
                'Code review and debugging',
                'Technical documentation',
            ])
        else:
            use_cases.extend([
                'General-purpose AI',
                'Conversational AI',
                'Content generation',
                'Question answering',
            ])
    
    # Qwen models
    elif 'qwen' in all_text:
        if 'coder' in all_text:
            use_cases.extend([
                'Code generation',
                'Programming tasks',
                'Software development',
            ])
        else:
            use_cases.extend([
                'Multilingual tasks',
                'General-purpose AI',
                'Content generation',
            ])
    
    # Amazon Nova models
    elif 'nova' in all_text:
        if 'sonic' in all_text:
            use_cases.extend([
                'Fast inference',
                'Real-time applications',
                'Low-latency tasks',
            ])
        elif 'lite' in all_text:
            use_cases.extend([
                'Lightweight applications',
                'Simple tasks',
                'Cost-effective solutions',
            ])
        else:
            use_cases.extend([
                'General-purpose AI',
                'Amazon ecosystem integration',
            ])
    
    # Amazon Titan models
    elif 'titan' in all_text:
        if 'embed' in all_text:
            use_cases.extend([
                'Text embeddings',
                'Semantic search',
                'Similarity matching',
                'Vector databases',
            ])
        else:
            use_cases.extend([
                'Text generation',
                'Content creation',
            ])
    
    # AI21 Jurassic models
    elif 'j2' in all_text or 'jurassic' in all_text:
        if 'ultra' in all_text:
            use_cases.extend([
                'Complex reasoning',
                'Advanced text generation',
                'Creative writing',
            ])
        else:
            use_cases.extend([
                'Text generation',
                'Content creation',
            ])
    
    # OpenAI GPT models
    elif 'gpt' in all_text or 'openai' in provider_lower:
        use_cases.extend([
            'General-purpose AI',
            'Conversational AI',
            'Content generation',
        ])
    
    # Check for specific capabilities based on modalities
    if input_modalities:
        if 'image' in str(input_modalities).lower():
            use_cases.append('Image understanding and analysis')
        if 'audio' in str(input_modalities).lower():
            use_cases.append('Audio processing')
    
    if output_modalities:
        if 'image' in str(output_modalities).lower():
            use_cases.append('Image generation')
    
    # Check description for specific use case keywords
    if 'embed' in desc_lower or 'embedding' in desc_lower:
        use_cases.append('Text embeddings and vector search')
    
    if 'code' in desc_lower or 'programming' in desc_lower:
        use_cases.append('Code-related tasks')
    
    if 'multimodal' in desc_lower or 'multi-modal' in desc_lower:
        use_cases.append('Multimodal tasks (text, images, etc.)')
    
    # Remove duplicates while preserving order (case-insensitive)
    seen = set()
    unique_use_cases = []
    for uc in use_cases:
        uc_lower = uc.lower().strip()
        if uc_lower and uc_lower not in seen:
            seen.add(uc_lower)
            unique_use_cases.append(uc.strip())
    
    # If no specific use cases found, provide generic ones
    if not unique_use_cases:
        unique_use_cases = [
            'General-purpose AI tasks',
            'Text generation',
            'Question answering',
        ]
    
    return unique_use_cases[:5]  # Limit to top 5 use cases

