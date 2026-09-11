from typing import TypedDict, Optional, List, Dict, Any

class AgentState(TypedDict, total=False):
    current_query:         str
    messages:              List[Dict[str, str]]
    category:              Optional[str]          
    route:                 Optional[str]
    metric:                Optional[str]
    raw_filters:           Dict[str, Any]
    top_n:                 Optional[int]
    intent:                Optional[Dict[str, Any]]
    rewritten_query:       Optional[str]
    validated_schema:      Dict[str, str]
    sql_queries:           List[Dict[str, str]]
    raw_db_results:        List[Any]
    query_result:          Optional[Any]
    final_response:        Optional[str]
    response_html:         Optional[str]
    record_count:          Optional[int]
    data:                  Optional[List[Any]]
    insights:              Optional[List[str]]
    filters_used:          Optional[Dict[str, Any]]
    clarification_options: Optional[List[str]]
    user_id:               str              
    language:              str       
    is_ambiguous:          bool
    user_selected_value:   Optional[Dict[str, Any]]
    turn_classification:   Optional[str]
    semantic_state:        Optional[Dict[str,Any]]
    
