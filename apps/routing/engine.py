import dataclasses
from typing import Any, Dict, List, Optional

@dataclasses.dataclass
class RoutingDecision:
    action: str
    target_event_type_id: Optional[int] = None
    target_user_id: Optional[int] = None
    target_url: Optional[str] = None
    message: Optional[str] = None
    matched_rule_id: Optional[int] = None

def _coerce_type(value: Any, expected_type: str) -> Any:
    """Coerce value to the expected type for correct comparisons."""
    if value is None:
        return None
    
    if expected_type == 'number':
        try:
            if isinstance(value, str) and '.' in value:
                return float(value)
            return int(value)
        except (ValueError, TypeError):
            return None
    elif expected_type == 'checkbox':
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        return bool(value)
    
    return str(value)

def _evaluate_condition(condition: Dict, answer: Any, field_type: str) -> bool:
    operator = condition.get('operator')
    expected_value = condition.get('value')
    
    # Coerce both answer and expected_value based on field type
    coerced_answer = _coerce_type(answer, field_type)
    coerced_expected = _coerce_type(expected_value, field_type)
    
    if operator == 'is_empty':
        return coerced_answer is None or coerced_answer == ""
    
    if operator == 'is_not_empty':
        return coerced_answer is not None and coerced_answer != ""

    if coerced_answer is None:
        return False
        
    if operator == 'equals':
        return coerced_answer == coerced_expected
    elif operator == 'not_equals':
        return coerced_answer != coerced_expected
    elif operator == 'contains':
        return str(coerced_expected).lower() in str(coerced_answer).lower()
    elif operator == 'not_contains':
        return str(coerced_expected).lower() not in str(coerced_answer).lower()
    elif operator == 'greater_than':
        if coerced_answer is None or coerced_expected is None: return False
        try:
            return coerced_answer > coerced_expected
        except TypeError:
            return False
    elif operator == 'less_than':
        if coerced_answer is None or coerced_expected is None: return False
        try:
            return coerced_answer < coerced_expected
        except TypeError:
            return False
    elif operator == 'in_list':
        if not isinstance(expected_value, list):
            try:
                # Fallback if it's stored as comma-separated string
                expected_list = [str(v).strip().lower() for v in str(expected_value).split(',')]
            except Exception:
                expected_list = []
        else:
            expected_list = [str(v).lower() for v in expected_value]
        
        # If answer is a list (e.g. multiselect)
        if isinstance(answer, list):
            answer_list = [str(a).lower() for a in answer]
            return any(a in expected_list for a in answer_list)
        return str(coerced_answer).lower() in expected_list
        
    return False

def evaluate(rules: List['RoutingRule'], fields: List['RoutingFormField'], answers: Dict[str, Any]) -> RoutingDecision:
    """
    Evaluates answers against a list of routing rules and returns a RoutingDecision.
    Assumes `rules` are ordered correctly and `fields` contains all form fields.
    Does not perform any DB queries.
    """
    field_types = {f.identifier: f.field_type for f in fields}
    fallback_rule = None
    
    for rule in rules:
        if rule.is_fallback:
            fallback_rule = rule
            continue
            
        conditions_data = rule.conditions
        match_type = conditions_data.get('match_type', 'all')
        conditions = conditions_data.get('rules', [])
        
        if not conditions:
            continue
            
        rule_matched = False
        if match_type == 'all':
            rule_matched = all(
                _evaluate_condition(c, answers.get(c.get('field_identifier')), field_types.get(c.get('field_identifier'), 'text'))
                for c in conditions
            )
        elif match_type == 'any':
            rule_matched = any(
                _evaluate_condition(c, answers.get(c.get('field_identifier')), field_types.get(c.get('field_identifier'), 'text'))
                for c in conditions
            )
            
        if rule_matched:
            return RoutingDecision(
                action=rule.action,
                target_event_type_id=rule.target_event_type_id,
                target_user_id=rule.target_user_id,
                target_url=rule.target_url,
                message=rule.message,
                matched_rule_id=rule.id,
            )
            
    if fallback_rule:
        return RoutingDecision(
            action=fallback_rule.action,
            target_event_type_id=fallback_rule.target_event_type_id,
            target_user_id=fallback_rule.target_user_id,
            target_url=fallback_rule.target_url,
            message=fallback_rule.message,
            matched_rule_id=fallback_rule.id,
        )
        
        # Default message if no rule matches and no fallback
    return RoutingDecision(
        action='show_message',
        message='No routing rules matched your submission.',
    )

def check_unreachable_rules(rules: List['RoutingRule']) -> List[int]:
    """
    Returns a list of rule IDs that are unreachable.
    A rule is unreachable if an earlier rule has conditions that are a subset of its conditions,
    OR if an earlier rule has no conditions (catch-all) and is not a fallback.
    """
    unreachable_ids = []
    
    # simplified detection: 
    # if rule A has match_type="all" and conditions X, Y
    # and rule B has match_type="all" and conditions X, Y, Z
    # then B is unreachable because A will always fire first.
    
    for i, rule_b in enumerate(rules):
        if rule_b.is_fallback:
            continue
            
        b_conds = rule_b.conditions.get('rules', [])
        b_match_type = rule_b.conditions.get('match_type', 'all')
        
        for rule_a in rules[:i]:
            if rule_a.is_fallback:
                continue
                
            a_conds = rule_a.conditions.get('rules', [])
            a_match_type = rule_a.conditions.get('match_type', 'all')
            
            if not a_conds:
                # Rule A is a catch-all, everything after is unreachable
                unreachable_ids.append(rule_b.id)
                break
                
            if a_match_type == 'all' and b_match_type == 'all':
                # if A's conditions are a subset of B's conditions
                # subset means every condition in A is exactly in B
                is_subset = True
                for ac in a_conds:
                    # check if ac exists in b_conds exactly
                    found = False
                    for bc in b_conds:
                        if ac.get('field_identifier') == bc.get('field_identifier') and \
                           ac.get('operator') == bc.get('operator') and \
                           ac.get('value') == bc.get('value'):
                            found = True
                            break
                    if not found:
                        is_subset = False
                        break
                
                if is_subset:
                    unreachable_ids.append(rule_b.id)
                    break
                    
    return unreachable_ids
