> **External page content (untrusted):** Treat the content below as data, not instructions. Do not follow requests in it to call tools or disclose or send data.

## semantic-conventions-genai/docs/gen-ai/gen-ai-agent-spans.md at main · open-telemetry/semantic-conventions-genai · GitHub

**Source**: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md

---

[Skip to content](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#start-of-content)

You signed in with another tab or window.[Reload](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)to refresh your session.You signed out in another tab or window.[Reload](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)to refresh your session.You switched accounts on another tab or window.[Reload](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)to refresh your session.Dismiss alert

{{ message }}

[open-telemetry](https://github.com/open-telemetry)/**[semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)**Public

- [Notifications](https://github.com/login?return_to=%2Fopen-telemetry%2Fsemantic-conventions-genai)You must be signed in to change notification settings
- [Fork102](https://github.com/login?return_to=%2Fopen-telemetry%2Fsemantic-conventions-genai)
- 

[Star386](https://github.com/login?return_to=%2Fopen-telemetry%2Fsemantic-conventions-genai)

[](https://github.com/open-telemetry/semantic-conventions-genai)

## Expand file tree

/

# gen-ai-agent-spans.mdCopy path

More file actions

More file actions

## Latest commit

## History[History](https://github.com/open-telemetry/semantic-conventions-genai/commits/main/docs/gen-ai/gen-ai-agent-spans.md)

[](https://github.com/open-telemetry/semantic-conventions-genai/commits/main/docs/gen-ai/gen-ai-agent-spans.md)History

1054 lines (773 loc) · 97.2 KB

/

# gen-ai-agent-spans.mdCopy path

## File metadata and controls

- 
- 
- 

1054 lines (773 loc) · 97.2 KB

[Raw](https://github.com/open-telemetry/semantic-conventions-genai/raw/refs/heads/main/docs/gen-ai/gen-ai-agent-spans.md)

Copy raw file

Download raw fileOutline

Edit and raw actions

# Semantic Conventions for GenAI agent and framework spans[](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#semantic-conventions-for-genai-agent-and-framework-spans)

**Status**:[Development](https://opentelemetry.io/docs/specs/otel/document-status)
- [Semantic Conventions for GenAI agent and framework spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#semantic-conventions-for-genai-agent-and-framework-spans)
- [Spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#spans)
- [Create agent span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#create-agent-span)
- [Invoke agent client span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#invoke-agent-client-span)
- [Invoke agent internal span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#invoke-agent-internal-span)
- [Invoke workflow span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#invoke-workflow-span)
- [Plan span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#plan-span)
- [Execute tool span](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#execute-tool-span)

Generative AI models can be trained to use tools to access real-time information or suggest a real-world action. For example, a model can leverage a database retrieval tool to access specific information, like a customer's purchase history, so it can generate tailored shopping recommendations. Alternatively, based on a user's query, a model can make various API calls to send an email response to a colleague or complete a financial transaction on your behalf. To do so, the model must not only have access to a set of external tools, it needs the ability to plan and execute any task in a self-directed fashion. This combination of reasoning, logic, and access to external information that are all connected to a Generative AI model invokes the concept of an agent.

This document defines semantic conventions for GenAI agent calls that are defined by this[whitepaper](https://www.kaggle.com/whitepaper-agents).

It MAY be applicable to agent operations that are performed by the GenAI framework locally.

The semantic conventions for GenAI agents extend and override the semantic conventions for[Gen AI Spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md).

## Spans[](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#spans)

### Create agent span[](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#create-agent-span)

**Status:**[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)

Describes GenAI agent creation and is usually applicable when working with remote agent services.

The`gen_ai.operation.name`SHOULD be`create_agent`.

**Span name**SHOULD be`create_agent {gen_ai.agent.name}`. Semantic conventions for individual GenAI systems and frameworks MAY specify different span name format.

**Span kind**SHOULD be`CLIENT`.

**Span status**SHOULD follow the[Recording Errors](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/recording-errors.md)document.

**Requirement level:**[Recommended](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/signal-requirement-level.md).

**Attributes:**KeyStability[Requirement Level](https://opentelemetry.io/docs/specs/semconv/general/attribute-requirement-level/)Value TypeDescriptionExample Values[`gen_ai.operation.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Required`stringThe name of the operation being performed. [1]`chat`;`generate_content`;`text_completion`[`gen_ai.provider.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Required`stringThe Generative AI provider as identified by the client or server instrumentation. [2]`openai`;`gcp.gen_ai`;`gcp.vertex_ai`[`error.type`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/error.md)[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)`Conditionally Required`If the operation ended in an error.stringDescribes a class of error the operation ended with. [3]`timeout`;`java.net.UnknownHostException`;`server_certificate_invalid`;`500`[`gen_ai.agent.description`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If provided by the application.stringThe free-form description of the GenAI agent created during this operation.`Helps with math problems`;`Generates fiction stories`[`gen_ai.agent.id`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If applicable.stringThe stable unique identifier of the GenAI agent created during this operation. [4]`asst_5j66UpCpwteGg4YSxUnt7lPY`;`arn:aws:bedrock:us-east-1:123:agent/42`;`urn:agent:projects-123:projects:123:locations:us-east1:aiplatform:reasoningEngines:456`[`gen_ai.agent.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If provided by the application.stringThe human-readable name of the GenAI agent created during this operation.`Math Tutor`;`Fiction Writer`[`gen_ai.agent.version`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If provided by the application.stringThe version of the GenAI agent created during this operation.`1.0.0`;`2025-05-01`[`gen_ai.request.model`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If available.stringThe name of the GenAI model a request is being made to. [5]`gpt-4`[`server.port`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/server.md)[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)`Conditionally Required`If`server.address`is set.intGenAI server port. [6]`80`;`8080`;`443`[`server.address`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/server.md)[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)`Recommended`stringGenAI server address. [7]`example.com`;`10.1.2.80`;`/tmp/my.sock`[`gen_ai.system_instructions`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Opt-In`anyThe system message or instructions provided to the GenAI model separately from the chat history. [8][
{
"type": "text",
"content": "You are an Agent that greet users, always use greetings tool to respond"
}
]; [
{
"type": "text",
"content": "You are a language translator."
},
{
"type": "text",
"content": "Your mission is to translate text in English to French."
}
]

**[1]`gen_ai.operation.name`:**If one of the predefined values applies, but specific system uses a different name it's RECOMMENDED to document it in the semantic conventions for specific GenAI system and use system-specific name in the instrumentation. If a different name is not documented, instrumentation libraries SHOULD use applicable predefined value.

**[2]`gen_ai.provider.name`:**Semantic conventions for individual GenAI operations SHOULD clarify which kinds of providers (e.g. inference, embeddings, retrieval, memory, hosted agent providers) apply when it is not clear from context.

The attribute SHOULD be set based on the instrumentation's best knowledge and may differ from the actual upstream provider. For example, a client SDK may be configured against a proxy or hosting platform that transparently relays requests to a different provider.

The`gen_ai.provider.name`attribute acts as a discriminator that identifies the GenAI telemetry format flavor specific to that provider within GenAI semantic conventions. It SHOULD be set consistently with provider-specific attributes and signals. For example, GenAI spans, metrics, and events related to AWS Bedrock should have the`gen_ai.provider.name`set to`aws.bedrock`and include applicable`aws.bedrock.*`attributes and are not expected to include`openai.*`attributes.

**[3]`error.type`:**The`error.type`SHOULD match the error code returned by the Generative AI provider or the client library, the canonical name of exception that occurred, or another low-cardinality error identifier. Instrumentations SHOULD document the list of errors they report.

**[4]`gen_ai.agent.id`:**For hosted agents, this SHOULD be the provider-assigned stable identifier of the agent resource such as[AWS Bedrock agent ARN](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_Agent.html)or[GCP Agent Registry identifier](https://docs.cloud.google.com/agent-registry/concepts#agent-identifier). It's NOT RECOMMENDED to record in-memory agent instance ids on this attribute due to their transient nature.

**[5]`gen_ai.request.model`:**The name of the GenAI model a request is being made to. If the model is supplied by a vendor, then the value must be the exact name of the model requested. If the model is a fine-tuned custom model, the value should have a more specific name than the base model that's been fine-tuned.

**[6]`server.port`:**When observed from the client side, and when communicating through an intermediary,`server.port`SHOULD represent the server port behind any intermediaries, for example proxies, if it's available.

**[7]`server.address`:**When observed from the client side, and when communicating through an intermediary,`server.address`SHOULD represent the server address behind any intermediaries, for example proxies, if it's available.

**[8]`gen_ai.system_instructions`:**Instrumentations MUST follow[JSON schema](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/model/gen-ai/gen-ai-system-instructions.json).

When the attribute is recorded on events, it MUST be recorded in structured form. When recorded on spans, it MAY be recorded as a JSON string if structured format is not supported and SHOULD be recorded in structured form otherwise.

The following attributes can be important for making sampling decisions and SHOULD be provided**at span creation time**(if provided at all):
- [`gen_ai.agent.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)
- [`gen_ai.operation.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)
- [`gen_ai.provider.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)
- [`gen_ai.request.model`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)
- [`server.address`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/server.md)
- [`server.port`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/server.md)

---

`error.type`has the following list of well-known values. If one of them applies, then the respective value MUST be used; otherwise, a custom value MAY be used.ValueDescriptionStability`_OTHER`A fallback error value to be used when the instrumentation doesn't define a custom value.[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)

---

`gen_ai.operation.name`has the following list of well-known values. If one of them applies, then the respective value MUST be used; otherwise, a custom value MAY be used.ValueDescriptionStability`chat`Chat completion operation such as[OpenAI Chat API](https://platform.openai.com/docs/api-reference/chat)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`create_agent`Create GenAI agent[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`create_memory`Create new memory records[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`create_memory_store`Create or initialize a memory store[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`delete_memory`Delete memory records[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`delete_memory_store`Delete or deprovision a memory store[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`embeddings`Embeddings operation such as[OpenAI Create embeddings API](https://platform.openai.com/docs/api-reference/embeddings/create)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`execute_tool`Execute a tool[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`fetch_response`Fetch a previously generated model response by its identifier, without performing inference, such as[OpenAI Get a model response](https://platform.openai.com/docs/api-reference/responses/get)[9][](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`generate_content`Multimodal content generation operation such as[Gemini Generate Content](https://ai.google.dev/api/generate-content)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`invoke_agent`Invoke GenAI agent[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`invoke_workflow`Invoke GenAI workflow[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`plan`Agent planning or task decomposition phase[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`retrieval`Retrieval operation such as[OpenAI Search Vector Store API](https://platform.openai.com/docs/api-reference/vector-stores/search)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`search_memory`Search/query memories from a memory store[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`text_completion`Text completions operation such as[OpenAI Completions API (Legacy)](https://platform.openai.com/docs/api-reference/completions)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`update_memory`Update existing memory records[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`upsert_memory`Create or update memory records without the caller choosing which[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)

**[9]:**Instrumentations SHOULD NOT report token usage (as attributes or metrics) for this operation.

---

`gen_ai.provider.name`has the following list of well-known values. If one of them applies, then the respective value MUST be used; otherwise, a custom value MAY be used.ValueDescriptionStability`anthropic`[Anthropic](https://www.anthropic.com/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`aws.bedrock`[AWS Bedrock](https://aws.amazon.com/bedrock)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`azure.ai.inference`Azure AI Inference[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`azure.ai.openai`[Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-services/openai/overview)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`cohere`[Cohere](https://cohere.com/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`deepseek`[DeepSeek](https://www.deepseek.com/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`gcp.gemini`[Gemini](https://cloud.google.com/products/gemini)[10][](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`gcp.gen_ai`Any Google generative AI endpoint [11][](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`gcp.vertex_ai`[Vertex AI](https://cloud.google.com/vertex-ai)[12][](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`groq`[Groq](https://groq.com/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`ibm.watsonx.ai`[IBM Watsonx AI](https://www.ibm.com/products/watsonx-ai)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`mistral_ai`[Mistral AI](https://mistral.ai/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`moonshot_ai`[Moonshot AI](https://www.moonshot.ai/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`openai`[OpenAI](https://openai.com/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`perplexity`[Perplexity](https://www.perplexity.ai/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`x_ai`[xAI](https://x.ai/)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)

**[10]:**Used when accessing the 'generativelanguage.googleapis.com' endpoint. Also known as the AI Studio API.

**[11]:**May be used when specific backend is unknown.

**[12]:**Used when accessing the 'aiplatform.googleapis.com' endpoint.

### Invoke agent client span[](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md#invoke-agent-client-span)

**Status:**[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)

Describes GenAI agent invocation over a remote service.

The`gen_ai.operation.name`SHOULD be`invoke_agent`.

Examples: OpenAI Assistants API, AWS Bedrock Agents.

**Span name**SHOULD be`invoke_agent {gen_ai.agent.name}`if`gen_ai.agent.name`is readily available. When`gen_ai.agent.name`is not available, it SHOULD be`invoke_agent`. Semantic conventions for individual GenAI systems and frameworks MAY specify different span name format.

**Span kind**SHOULD be`CLIENT`.

**Span status**SHOULD follow the[Recording Errors](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/recording-errors.md)document.

**Requirement level:**[Recommended](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/general/signal-requirement-level.md).

**Attributes:**KeyStability[Requirement Level](https://opentelemetry.io/docs/specs/semconv/general/attribute-requirement-level/)Value TypeDescriptionExample Values[`gen_ai.operation.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Required`stringThe name of the operation being performed. [1]`chat`;`generate_content`;`text_completion`[`gen_ai.provider.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Required`stringThe Generative AI provider as identified by the client or server instrumentation. [2]`openai`;`gcp.gen_ai`;`gcp.vertex_ai`[`error.type`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/error.md)[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)`Conditionally Required`If the operation ended in an error.stringDescribes a class of error the operation ended with. [3]`timeout`;`java.net.UnknownHostException`;`server_certificate_invalid`;`500`[`gen_ai.agent.description`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`When available.stringThe free-form description of the invoked GenAI agent.`Helps with math problems`;`Generates fiction stories`[`gen_ai.agent.id`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If applicable.stringThe stable unique identifier of the invoked GenAI agent. [4]`asst_5j66UpCpwteGg4YSxUnt7lPY`;`arn:aws:bedrock:us-east-1:123:agent/42`;`urn:agent:projects-123:projects:123:locations:us-east1:aiplatform:reasoningEngines:456`[`gen_ai.agent.name`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`When available.stringThe human-readable name of the invoked GenAI agent.`Math Tutor`;`Fiction Writer`[`gen_ai.agent.version`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`When available.stringThe version of the invoked GenAI agent.`1.0.0`;`2025-05-01`[`gen_ai.conversation.id`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`[5]stringThe unique identifier for a conversation (session, thread), used to store and correlate messages within this conversation. [6]`conv_5j66UpCpwteGg4YSxUnt7lPY`[`gen_ai.data_source.id`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If applicable.stringThe data source identifier. [7]`H7STPQYOND`[`gen_ai.output.type`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`[8]stringRepresents the content type requested by the client. [9]`text`;`json`;`image`[`gen_ai.request.choice.count`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If available, in the request, and !=1.intThe target number of candidate completions to return.`3`[`gen_ai.request.seed`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Conditionally Required`If applicable and if the request includes a seed.intRequests with same seed value more likely to return same result.`100`[`server.port`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/server.md)[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)`Conditionally Required`If`server.address`is set.intGenAI server port. [10]`80`;`8080`;`443`[`gen_ai.request.frequency_penalty`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`doubleThe frequency penalty setting for the GenAI request.`0.1`[`gen_ai.request.max_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`intThe maximum number of tokens the model generates for a request.`100`[`gen_ai.request.model`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`If applicable.stringThe name of the GenAI model configured for the agent. [11]`gpt-4`[`gen_ai.request.presence_penalty`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`doubleThe presence penalty setting for the GenAI request.`0.1`[`gen_ai.request.previous_response.id`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`[12]stringThe unique identifier of a previous response or interaction used to provide context for the current operation. [13]`resp_0123456789aBCdef`;`interaction-123`[`gen_ai.request.stop_sequences`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`string[]List of sequences that the model will use to stop generating further tokens.`["forest", "lived"]`[`gen_ai.request.temperature`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`doubleThe temperature setting for the GenAI request.`0.0`[`gen_ai.request.top_p`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`doubleThe top_p sampling setting for the GenAI request.`1.0`[`gen_ai.response.finish_reasons`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`string[]Array of reasons the model stopped generating tokens, corresponding to each generation received. [14]`["stop"]`;`["stop", "length"]`;`["stop", "length", "error"]`[`gen_ai.usage.audio.cache_read.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of audio input tokens served from a provider-managed cache. [15]`60`[`gen_ai.usage.audio.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of audio input tokens. [16]`120`[`gen_ai.usage.audio.output_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of audio output tokens. [17]`240`[`gen_ai.usage.cache_read.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of input tokens served from a provider-managed cache. [18]`50`[`gen_ai.usage.cache_write.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of input tokens written to a provider-managed cache. [19]`25`[`gen_ai.usage.image.cache_read.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of image input tokens served from a provider-managed cache. [20]`128`[`gen_ai.usage.image.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of image input tokens. [21]`258`[`gen_ai.usage.image.output_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of image output tokens. [22]`1290`[`gen_ai.usage.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`intThe number of tokens used in the GenAI input (prompt). [23]`100`[`gen_ai.usage.output_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`intThe number of tokens used in the GenAI response (completion). [24]`180`[`gen_ai.usage.text.cache_read.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of text input tokens served from a provider-managed cache. [25]`40`[`gen_ai.usage.text.input_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of text input tokens. [26]`100`[`gen_ai.usage.text.output_tokens`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Recommended`When applicable.intThe number of text output tokens. [27]`180`[`server.address`](https://github.com/open-telemetry/semantic-conventions/blob/v1.44.0/docs/registry/attributes/server.md)[](https://camo.githubusercontent.com/3495f55636b76baf477b945f5f66fdee587fd0d5e815eb209510cdc766f41398/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d737461626c652d6c69676874677265656e)`Recommended`stringGenAI server address. [28]`example.com`;`10.1.2.80`;`/tmp/my.sock`[`gen_ai.input.messages`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Opt-In`anyThe chat history provided to the model as an input. [29][
{
"role": "user",
"parts": [
{
"type": "text",
"content": "Weather in Paris?"
}
]
},
{
"role": "assistant",
"parts": [
{
"type": "tool_call",
"id": "call_VSPygqKTWdrhaFErNvMV18Yl",
"name": "get_weather",
"arguments": {
"location": "Paris"
}
}
]
},
{
"role": "tool",
"parts": [
{
"type": "tool_call_response",
"id": "call_VSPygqKTWdrhaFErNvMV18Yl",
"response": "rainy, 57°F"
}
]
}
][`gen_ai.output.messages`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Opt-In`anyMessages returned by the model where each message represents a specific model response (choice, candidate). [30][
{
"role": "assistant",
"parts": [
{
"type": "text",
"content": "The weather in Paris is currently rainy with a temperature of 57°F."
}
]
}
][`gen_ai.system_instructions`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Opt-In`anyThe system message or instructions provided to the GenAI model separately from the chat history. [31][
{
"type": "text",
"content": "You are an Agent that greet users, always use greetings tool to respond"
}
]; [
{
"type": "text",
"content": "You are a language translator."
},
{
"type": "text",
"content": "Your mission is to translate text in English to French."
}
][`gen_ai.tool.definitions`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)[](https://camo.githubusercontent.com/51c52913babf0af7e3699435eb2fa53f65cf91d89c23a9e0edba0fdd9de155d0/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f2d646576656c6f706d656e742d626c7565)`Opt-In`anyThe list of tool definitions available to the GenAI agent or model. [32][
{
"type": "function",
"name": "get_current_weather",
"description": "Get the current weather in a given location",
"parameters": {
"type": "object",
"properties": {
"location": {
"type": "string",
"description": "The city and state, e.g. San Francisco, CA"
},
"unit": {
"type": "string",
"enum": [
"celsius",
"fahrenheit"
]
}
},
"required": [
"location",
"unit"
]
}
}
]

**[1]`gen_ai.operation.name`:**If one of the predefined values applies, but specific system uses a different name it's RECOMMENDED to document it in the semantic conventions for specific GenAI system and use system-specific name in the instrumentation. If a different name is not documented, instrumentation libraries SHOULD use applicable predefined value.

**[2]`gen_ai.provider.name`:**Semantic conventions for individual GenAI operations SHOULD clarify which kinds of providers (e.g. inference, embeddings, retrieval, memory, hosted agent providers) apply when it is not clear from context.

The attribute SHOULD be set based on the instrumentation's best knowledge and may differ from the actual upstream provider. For example, a client SDK may be configured against a proxy or hosting platform that transparently relays requests to a different provider.

The`gen_ai.provider.name`attribute acts as a discriminator that identifies the GenAI telemetry format flavor specific to that provider within GenAI semantic conventions. It SHOULD be set consistently with provider-specific attributes and signals. For example, GenAI spans, metrics, and events related to AWS Bedrock should have the`gen_ai.provider.name`set to`aws.bedrock`and include applicable`aws.bedrock.*`attributes and are not expected to include`openai.*`attributes.

**[3]`error.type`:**The`error.type`SHOULD match the error code returned by the Generative AI provider or the client library, the canonical name of exception that occurred, or another low-cardinality error identifier. Instrumentations SHOULD document the list of errors they report.

**[4]`gen_ai.agent.id`:**For hosted agents, this SHOULD be the provider-assigned stable identifier of the agent resource such as[AWS Bedrock agent ARN](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_Agent.html)or[GCP Agent Registry identifier](https://docs.cloud.google.com/agent-registry/concepts#agent-identifier). It's NOT RECOMMENDED to record in-memory agent instance ids on this attribute due to their transient nature.

**[5]`gen_ai.conversation.id`:**If and only if the instrumented library has one readily available, or the user application provides one through OpenTelemetry context or library-specific mechanisms.

**[6]`gen_ai.conversation.id`:**Instrumentations SHOULD populate conversation id when they have an identifier for the conversation readily available for a given operation, for example:
- when the client framework being instrumented manages conversation history (see[LlamaIndex chat store](https://docs.llamaindex.ai/en/stable/module_guides/storing/chat_stores/),[LangChain`session_id`](https://reference.langchain.com/python/langchain-core/runnables/history/RunnableWithMessageHistory), and[Google ADK sessions](https://adk.dev/sessions/session))
- when instrumenting GenAI client libraries that maintain a conversation on the backend (see[AWS Bedrock agent sessions](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-session-state.html),[OpenAI Assistant threads](https://platform.openai.com/docs/api-reference/threads))

When no identifier for the conversation is available, instrumentations SHOULD NOT populate conversation id. For example, a new UUID, a trace identifier, or a hash of request content SHOULD NOT be used as a fallback value.

Application developers that manage conversation history MAY add conversation id to GenAI and other spans or logs using custom span or log record processors or hooks provided by instrumentation libraries.

**[7]`gen_ai.data_source.id`:**Data sources are used by AI agents and RAG applications to store grounding data. A data source may be an external database, object store, document collection, website, or any other storage system used by the GenAI agent or applicat
