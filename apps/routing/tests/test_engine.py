import pytest
from apps.routing.engine import evaluate, RoutingDecision

class MockField:
    def __init__(self, identifier, field_type):
        self.identifier = identifier
        self.field_type = field_type

class MockRule:
    def __init__(self, id, is_fallback, conditions, action, target_event_type_id=None, target_user_id=None, target_url=None, message=None):
        self.id = id
        self.is_fallback = is_fallback
        self.conditions = conditions
        self.action = action
        self.target_event_type_id = target_event_type_id
        self.target_user_id = target_user_id
        self.target_url = target_url
        self.message = message

def test_engine_basic_match():
    fields = [MockField("company_size", "number")]
    rules = [
        MockRule(1, False, {"match_type": "all", "rules": [{"field_identifier": "company_size", "operator": "greater_than", "value": "50"}]}, "route_to_member", target_user_id=1),
        MockRule(2, False, {"match_type": "all", "rules": [{"field_identifier": "company_size", "operator": "less_than", "value": "50"}]}, "route_to_member", target_user_id=2),
    ]
    
    # Test greater than 50
    decision = evaluate(rules, fields, {"company_size": "100"})
    assert decision.matched_rule_id == 1
    assert decision.target_user_id == 1
    
    # Test type coercion (comparing "9" with "50")
    # "9" as string is > "50" as string, but as numbers 9 < 50
    decision = evaluate(rules, fields, {"company_size": "9"})
    assert decision.matched_rule_id == 2
    assert decision.target_user_id == 2

def test_engine_fallback():
    fields = [MockField("name", "text")]
    rules = [
        MockRule(1, False, {"match_type": "all", "rules": [{"field_identifier": "name", "operator": "equals", "value": "Alice"}]}, "show_message", message="Hi Alice"),
        MockRule(2, True, {}, "show_message", message="Fallback"),
    ]
    
    decision = evaluate(rules, fields, {"name": "Bob"})
    assert decision.matched_rule_id == 2
    assert decision.message == "Fallback"
    
def test_engine_no_match_no_fallback():
    fields = [MockField("name", "text")]
    rules = [
        MockRule(1, False, {"match_type": "all", "rules": [{"field_identifier": "name", "operator": "equals", "value": "Alice"}]}, "show_message", message="Hi Alice"),
    ]
    decision = evaluate(rules, fields, {"name": "Bob"})
    assert decision.matched_rule_id is None
    assert decision.action == "show_message"
    assert "No routing rules matched" in decision.message

def test_engine_missing_field():
    fields = [MockField("name", "text"), MockField("age", "number")]
    rules = [
        MockRule(1, False, {"match_type": "all", "rules": [{"field_identifier": "age", "operator": "greater_than", "value": 18}]}, "show_message", message="Adult"),
    ]
    decision = evaluate(rules, fields, {"name": "Bob"}) # age is missing
    # Should not crash, should not match
    assert decision.matched_rule_id is None

def test_engine_match_type_any():
    fields = [MockField("a", "text"), MockField("b", "text")]
    rules = [
        MockRule(1, False, {"match_type": "any", "rules": [
            {"field_identifier": "a", "operator": "equals", "value": "yes"},
            {"field_identifier": "b", "operator": "equals", "value": "yes"}
        ]}, "route_to_external_url", target_url="http://example.com"),
    ]
    
    decision1 = evaluate(rules, fields, {"a": "yes", "b": "no"})
    assert decision1.matched_rule_id == 1
    
    decision2 = evaluate(rules, fields, {"a": "no", "b": "no"})
    assert decision2.matched_rule_id is None

def test_engine_in_list():
    fields = [MockField("color", "select")]
    rules = [
        MockRule(1, False, {"match_type": "all", "rules": [{"field_identifier": "color", "operator": "in_list", "value": ["red", "blue"]}]}, "show_message"),
    ]
    
    assert evaluate(rules, fields, {"color": "RED"}).matched_rule_id == 1
    assert evaluate(rules, fields, {"color": "green"}).matched_rule_id is None
