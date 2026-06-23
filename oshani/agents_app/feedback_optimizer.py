"""Agent optimization using feedback loop."""
import logging
from .models import AgentFeedback

logger = logging.getLogger(__name__)


class FeedbackOptimizer:
    """Optimize agent responses based on user feedback."""
    
    def __init__(self, agent):
        self.agent = agent
    
    def analyze_feedback(self):
        """Analyze feedback patterns for the agent."""
        feedbacks = AgentFeedback.objects.filter(agent=self.agent)
        
        total_feedbacks = feedbacks.count()
        if total_feedbacks == 0:
            return {
                'total': 0,
                'positive_count': 0,
                'negative_count': 0,
                'neutral_count': 0,
                'positive_ratio': 0.0,
                'negative_ratio': 0.0,
                'recent_trend': 'insufficient_data',
                'common_issues': [],
                'recommendations': []
            }
        
        positive_count = feedbacks.filter(feedback_type='positive').count()
        negative_count = feedbacks.filter(feedback_type='negative').count()
        neutral_count = feedbacks.filter(feedback_type='neutral').count()
        
        positive_ratio = positive_count / total_feedbacks if total_feedbacks > 0 else 0
        negative_ratio = negative_count / total_feedbacks if total_feedbacks > 0 else 0
        
        # Analyze recent trend (last 10 feedbacks)
        recent_feedbacks = feedbacks.order_by('-created_at')[:10]
        recent_positive = sum(1 for f in recent_feedbacks if f.feedback_type == 'positive')
        recent_negative = sum(1 for f in recent_feedbacks if f.feedback_type == 'negative')
        
        if recent_positive > recent_negative:
            recent_trend = 'improving'
        elif recent_negative > recent_positive:
            recent_trend = 'declining'
        else:
            recent_trend = 'stable'
        
        # Extract common issues from negative feedback
        negative_feedbacks = feedbacks.filter(feedback_type='negative')
        common_issues = self._extract_common_issues(negative_feedbacks)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            positive_ratio, negative_ratio, recent_trend, common_issues
        )
        
        return {
            'total': total_feedbacks,
            'positive_count': positive_count,
            'negative_count': negative_count,
            'neutral_count': neutral_count,
            'positive_ratio': positive_ratio,
            'negative_ratio': negative_ratio,
            'recent_trend': recent_trend,
            'common_issues': common_issues,
            'recommendations': recommendations
        }
    
    def _extract_common_issues(self, negative_feedbacks):
        """Extract common issues from negative feedback."""
        issues = []
        
        # Analyze feedback text for patterns
        feedback_texts = [f.feedback_text.lower() for f in negative_feedbacks if f.feedback_text]
        
        # Common issue keywords
        issue_patterns = {
            'incomplete': ['incomplete', 'missing', 'partial', 'not enough'],
            'incorrect': ['wrong', 'incorrect', 'error', 'mistake', 'false'],
            'irrelevant': ['irrelevant', 'off-topic', 'not related', 'doesn\'t answer'],
            'unclear': ['unclear', 'confusing', 'hard to understand', 'vague'],
            'too_short': ['too short', 'brief', 'not detailed', 'insufficient'],
            'too_long': ['too long', 'verbose', 'wordy', 'rambling'],
        }
        
        for issue_type, keywords in issue_patterns.items():
            count = sum(1 for text in feedback_texts if any(kw in text for kw in keywords))
            if count > 0:
                issues.append({
                    'type': issue_type,
                    'count': count,
                    'percentage': (count / len(negative_feedbacks) * 100) if negative_feedbacks.count() > 0 else 0
                })
        
        # Sort by count
        issues.sort(key=lambda x: x['count'], reverse=True)
        return issues[:5]  # Top 5 issues
    
    def _generate_recommendations(self, positive_ratio, negative_ratio, recent_trend, common_issues):
        """Generate optimization recommendations."""
        recommendations = []
        
        if negative_ratio > 0.3:  # More than 30% negative feedback
            recommendations.append({
                'priority': 'high',
                'action': 'Review and update agent instructions',
                'description': f'High negative feedback ratio ({negative_ratio:.1%}). Consider refining the agent\'s instructions.'
            })
        
        if recent_trend == 'declining':
            recommendations.append({
                'priority': 'high',
                'action': 'Investigate recent changes',
                'description': 'Recent feedback shows declining trend. Review recent interactions.'
            })
        
        # Issue-specific recommendations
        for issue in common_issues:
            if issue['type'] == 'incomplete':
                recommendations.append({
                    'priority': 'medium',
                    'action': 'Enhance response completeness',
                    'description': f"{issue['percentage']:.1f}% of negative feedback mentions incomplete responses. Add instructions to provide comprehensive answers."
                })
            elif issue['type'] == 'incorrect':
                recommendations.append({
                    'priority': 'high',
                    'action': 'Improve accuracy',
                    'description': f"{issue['percentage']:.1f}% of negative feedback mentions incorrect information. Review training data and add fact-checking instructions."
                })
            elif issue['type'] == 'irrelevant':
                recommendations.append({
                    'priority': 'medium',
                    'action': 'Improve relevance',
                    'description': f"{issue['percentage']:.1f}% of negative feedback mentions irrelevant responses. Add instructions to stay on topic."
                })
            elif issue['type'] == 'unclear':
                recommendations.append({
                    'priority': 'medium',
                    'action': 'Improve clarity',
                    'description': f"{issue['percentage']:.1f}% of negative feedback mentions unclear responses. Add instructions for clear, concise communication."
                })
            elif issue['type'] == 'too_short':
                recommendations.append({
                    'priority': 'low',
                    'action': 'Increase detail level',
                    'description': f"{issue['percentage']:.1f}% of negative feedback mentions responses being too short. Add instructions to provide more detail."
                })
            elif issue['type'] == 'too_long':
                recommendations.append({
                    'priority': 'low',
                    'action': 'Reduce verbosity',
                    'description': f"{issue['percentage']:.1f}% of negative feedback mentions responses being too long. Add instructions to be more concise."
                })
        
        if positive_ratio > 0.7:  # More than 70% positive
            recommendations.append({
                'priority': 'low',
                'action': 'Maintain current approach',
                'description': 'High positive feedback ratio. Current configuration is working well.'
            })
        
        return recommendations
    
    def optimize_agent_instructions(self):
        """Optimize agent instructions based on feedback analysis."""
        analysis = self.analyze_feedback()
        
        if analysis['total'] < 5:  # Need at least 5 feedbacks to optimize
            return {
                'optimized': False,
                'reason': 'Insufficient feedback data (need at least 5 feedbacks)',
                'analysis': analysis
            }
        
        current_instruction = self.agent.configuration.get('instruction', '') or self.agent.configuration.get('system_prompt', '')
        
        # Build optimized instruction
        optimized_parts = []
        
        # Start with base instruction
        if current_instruction:
            optimized_parts.append(current_instruction)
        
        # Add optimization based on feedback
        optimization_notes = []
        
        # Handle common issues
        for issue in analysis['common_issues']:
            if issue['type'] == 'incomplete':
                optimization_notes.append("Always provide complete, comprehensive answers. Include all relevant details.")
            elif issue['type'] == 'incorrect':
                optimization_notes.append("Ensure all information provided is accurate and factually correct. If uncertain, state that clearly.")
            elif issue['type'] == 'irrelevant':
                optimization_notes.append("Stay focused on the user's question. Do not provide information that is not directly relevant.")
            elif issue['type'] == 'unclear':
                optimization_notes.append("Communicate clearly and concisely. Use simple language and structure your responses logically.")
            elif issue['type'] == 'too_short':
                optimization_notes.append("Provide sufficient detail in your responses. Elaborate on key points when helpful.")
            elif issue['type'] == 'too_long':
                optimization_notes.append("Be concise while still being thorough. Avoid unnecessary verbosity.")
        
        # Add optimization notes if any
        if optimization_notes:
            optimized_instruction = current_instruction
            if optimized_instruction:
                optimized_instruction += "\n\nAdditional Guidelines Based on User Feedback:\n"
            else:
                optimized_instruction = "Guidelines Based on User Feedback:\n"
            
            for i, note in enumerate(optimization_notes, 1):
                optimized_instruction += f"{i}. {note}\n"
            
            # Update agent configuration
            if not self.agent.configuration:
                self.agent.configuration = {}
            
            self.agent.configuration['instruction'] = optimized_instruction
            self.agent.configuration['system_prompt'] = optimized_instruction
            self.agent.configuration['last_optimized'] = str(self.agent.updated_at)
            self.agent.configuration['optimization_based_on_feedbacks'] = analysis['total']
            self.agent.save(update_fields=['configuration', 'updated_at'])
            
            return {
                'optimized': True,
                'reason': 'Instructions updated based on feedback analysis',
                'analysis': analysis,
                'optimization_notes': optimization_notes,
                'new_instruction': optimized_instruction
            }
        else:
            return {
                'optimized': False,
                'reason': 'No significant issues found to optimize',
                'analysis': analysis
            }
    
    def get_enhanced_context(self, query):
        """Get enhanced context for query based on positive feedback patterns."""
        # Find similar queries with positive feedback
        positive_feedbacks = AgentFeedback.objects.filter(
            agent=self.agent,
            feedback_type='positive'
        ).order_by('-created_at')[:10]
        
        # Extract patterns from positive feedback
        context_enhancements = []
        
        for feedback in positive_feedbacks:
            # Check if query is similar (simple keyword matching for now)
            query_words = set(query.lower().split())
            feedback_query_words = set(feedback.query.lower().split())
            
            # If there's significant overlap, this was a good response pattern
            overlap = len(query_words & feedback_query_words)
            if overlap >= 2:  # At least 2 common words
                context_enhancements.append({
                    'similar_query': feedback.query,
                    'good_response': feedback.response[:200],  # First 200 chars
                    'relevance': overlap / max(len(query_words), len(feedback_query_words))
                })
        
        return context_enhancements
    
    def should_use_enhanced_prompt(self):
        """Determine if enhanced prompt should be used based on feedback."""
        analysis = self.analyze_feedback()
        
        # Use enhanced prompt if:
        # 1. We have enough feedback (at least 5)
        # 2. Negative ratio is significant (>20%)
        # 3. Or recent trend is declining
        return (
            analysis['total'] >= 5 and
            (analysis['negative_ratio'] > 0.2 or analysis['recent_trend'] == 'declining')
        )




