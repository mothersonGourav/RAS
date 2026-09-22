from langgraph.graph import StateGraph, END
from app.core.state import AgentState
from app.graph.nodes import HarmoniaGraphNodes,RASGraphNodes

class HarmoniaSQLOrchestrator:
    def __init__(self, nodes: HarmoniaGraphNodes):
        self.nodes = nodes
        self.graph = self.build_graph()

    def build_graph(self):
        workflow = StateGraph(AgentState)
        
        # Register nodes
        workflow.add_node("guardrail", self.nodes.guardrail_node)     
        workflow.add_node("rewrite", self.nodes.rewrite_node)
        workflow.add_node("intent", self.nodes.intent_node)
        workflow.add_node("vector", self.nodes.vector_node)
        workflow.add_node("sql", self.nodes.sql_node)
        workflow.add_node("execute", self.nodes.execute_node)
        workflow.add_node("synthesize", self.nodes.synthesize_node)
        workflow.add_node("cache_save", self.nodes.cache_save_node)


        workflow.set_entry_point("guardrail")
        
        def route_guardrail(state: AgentState):
                    if state.get("category") == "security_block":
                        return "blocked"
                    return "safe"
                    
        workflow.add_conditional_edges(
                "guardrail", route_guardrail,
                {
                    "blocked": END,         
                    "safe": "rewrite"    
                }
            )
        
        workflow.add_edge("rewrite", "intent")
        
        # 5. Intent Routing: Handles Greetings, Off-topic, or Database Queries
        def route_intent(state: AgentState):
            category = state.get("category", "database_query")
            if category in ["greeting", "vague_or_off_topic","security_block"]:
                return "fast_track"
            if state.get("route") == "vague":
                return "vague"
            if state.get("raw_filters"):
                return "has_filters"
            return "no_filters"
                       
        workflow.add_conditional_edges(
            "intent", route_intent,
            {
                "fast_track": "synthesize",
                "vague": "synthesize",
                "has_filters": "vector",
                "no_filters": "sql"
            }
        )

        # 6. Vector Routing 
        def route_vector(state: AgentState):
            if state.get("route") == "ambiguous":
                return "ambiguous_end" 
            return "sql"                
            
        workflow.add_conditional_edges(
            "vector", route_vector,
            {"ambiguous_end": END, "sql": "sql"}
        )
        
        # 7. Final Execution and Output Generation
        workflow.add_edge("sql", "execute")
        workflow.add_edge("execute", "synthesize")
        workflow.add_edge("synthesize", "cache_save")
        workflow.add_edge("synthesize", END)

        return workflow.compile()
        
    async def invoke(self, state: dict):
        return await self.graph.ainvoke(state)


class RASSQLOrchestrator:
    def __init__(self, nodes: RASGraphNodes):
        self.nodes = nodes
        self.graph = self.build_graph()

    def build_graph(self):
        workflow = StateGraph(AgentState)

        # Register nodes
        workflow.add_node("guardrail",        self.nodes.guardrail_node)
        workflow.add_node("rewrite",          self.nodes.rewrite_node)
        workflow.add_node("intent",           self.nodes.intent_node)
        workflow.add_node("vector",           self.nodes.vector_node)
        workflow.add_node("sql",              self.nodes.sql_node)
        workflow.add_node("execute",          self.nodes.execute_node)
        workflow.add_node("synthesize",       self.nodes.synthesize_node)
        workflow.add_node("cache_save",  self.nodes.cache_save_node)

        # Entry point 
        workflow.set_entry_point("guardrail")

        # Guardrail routing 
        def route_guardrail(state: AgentState):
            if state.get("category") == "security_block":
                return "blocked"
            return "safe"

        workflow.add_conditional_edges(
            "guardrail",
            route_guardrail,
            {
                "blocked": END, 
                "safe":    "rewrite",
            },
        )

        #  Rewrite -> Intent
        workflow.add_edge("rewrite","intent")

        # Intent routing
        def route_intent(state: AgentState):
            if state.get("route") == "failed":
                return "failed"
            if state.get("route") == "vague":
                return "vague"
            if state.get("raw_filters"):
                return "has_filters"
            return "no_filters"

        workflow.add_conditional_edges(
            "intent",
            route_intent,
            {
                "failed":      "synthesize",
                "vague":       "synthesize",
                "has_filters": "vector",
                "no_filters":  "sql",
            },
        )

        # Vector routing 
        def route_vector(state: AgentState):
            if state.get("route") == "ambiguous":
                return "ambiguous_end"
            return "sql"

        workflow.add_conditional_edges(
            "vector",
            route_vector,
            {
                "ambiguous_end": END,
                "sql":           "sql",
            },
        )
        workflow.add_edge("sql",       "execute")
        workflow.add_edge("execute",   "synthesize")
        workflow.add_edge("synthesize", "cache_save") 
        workflow.add_edge("cache_save", END)
        return workflow.compile()

    async def invoke(self, state: dict):
        return await self.graph.ainvoke(state)