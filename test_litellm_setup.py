#!/usr/bin/env python3
"""
Script to test LiteLLM proxy setup with your virtual key.

This script tests all the models you've configured:
- gpt-4o
- gpt-4o-mini
- text-embedding-3-large
- text-embedding-3-small

Usage:
    python test_litellm_setup.py
""" 

import os
import sys
import json
import httpx
from openai import OpenAI

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Load .env from the same directory as this script
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(env_path)
    print("✅ Loaded environment variables from .env file\n")
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
    print("   Falling back to system environment variables\n")

# ========================================================================
# CONFIGURATION - Loaded from environment variables
# ========================================================================

# Your LiteLLM proxy URL
LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://litellm.amzur.com:4000")

# Your virtual key from LiteLLM
VIRTUAL_KEY = os.getenv("LITELLM_VIRTUAL_KEY")
if not VIRTUAL_KEY:
    print("❌ LITELLM_VIRTUAL_KEY not set. Add it to your .env file.")
    sys.exit(1)

# User identification
USER_ID = os.getenv("LITELLM_USER_ID")
if not USER_ID:
    print("❌ LITELLM_USER_ID not set. Add it to your .env file.")
    sys.exit(1)

USER_METADATA = {
    "department": os.getenv("LITELLM_DEPARTMENT", "Development"),
    "environment": os.getenv("LITELLM_ENVIRONMENT", "testing"),
    "application": "litellm-test-script"
}
# Extra headers must be JSON strings, not dictionaries
SPEND_LOGS_METADATA = json.dumps({
    "end_user": USER_ID,
    "department": USER_METADATA["department"],
    "environment": USER_METADATA["environment"]
})

# Models to test
CHAT_MODELS = ["gpt-4o", "gpt-4o-mini", "gemini/gemini-2.5-flash"]
EMBEDDING_MODELS = ["text-embedding-3-large", "text-embedding-3-small"]

# ========================================================================


def test_chat_completion(client: OpenAI, model: str):
    """Test a chat completion with the given model."""
    print(f"\n{'='*80}")
    print(f"Testing Chat Model: {model}")
    print(f"{'='*80}")
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'Hello from LiteLLM!' and tell me what model you are."}
            ],
            max_tokens=100,
            temperature=0.7,
            user=USER_ID,  # Track which user made this request
            extra_body={
                "metadata": USER_METADATA  # Additional metadata for tracking
            },
            extra_headers={
                "x-litellm-spend-logs-metadata": SPEND_LOGS_METADATA # Additional metadata for spend logs
            } 
        )
        
        print("✅ SUCCESS!")
        print(f"Model: {response.model}")
        print(f"Response: {response.choices[0].message.content}")
        print(f"Tokens used: {response.usage.total_tokens}")
        print(f"  - Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  - Completion tokens: {response.usage.completion_tokens}")

        return True
        
    except Exception as e:
        print(f"❌ FAILED!")
        print(f"Error: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        return False


def test_streaming_completion(client: OpenAI, model: str):
    """Test a streaming chat completion with the given model."""
    print(f"\n{'='*80}")
    print(f"Testing Streaming with Model: {model}")
    print(f"{'='*80}")
    
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "Count from 1 to 5 slowly."}
            ],
            max_tokens=50,
            stream=True,
            user=USER_ID,  # Track which user made this request
            extra_body={
                "metadata": {**USER_METADATA, "test_type": "streaming"}
            },
            extra_headers={
                "x-litellm-spend-logs-metadata": SPEND_LOGS_METADATA
            }       
        )
        
        print("✅ Streaming response:")
        print("Response: ", end="", flush=True)
        
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                print(chunk.choices[0].delta.content, end="", flush=True)
        
        print("\n✅ Streaming completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ FAILED!")
        print(f"Error: {str(e)}")
        return False


def test_embedding(client: OpenAI, model: str):
    """Test an embedding with the given model."""
    print(f"\n{'='*80}")
    print(f"Testing Embedding Model: {model}")
    print(f"{'='*80}")
    
    try:
        response = client.embeddings.create(
            model=model,
            input="This is a test sentence for embedding.",
            user=USER_ID,  # Track which user made this request
            extra_body={
                "metadata": {**USER_METADATA, "test_type": "single_embedding"}
            },
            extra_headers={
                "x-litellm-spend-logs-metadata": SPEND_LOGS_METADATA
            }       

        )
        
        embedding = response.data[0].embedding
        
        print("✅ SUCCESS!")
        print(f"Model: {response.model}")
        print(f"Embedding dimensions: {len(embedding)}")
        print(f"First 5 values: {embedding[:5]}")
        print(f"Total tokens used: {response.usage.total_tokens}")
        
        return True
        
    except Exception as e:
        print(f"❌ FAILED!")
        print(f"Error: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        return False


def test_batch_embeddings(client: OpenAI, model: str):
    """Test batch embeddings with multiple inputs."""
    print(f"\n{'='*80}")
    print(f"Testing Batch Embeddings with Model: {model}")
    print(f"{'='*80}")
    
    try:
        texts = [
            "First test sentence.",
            "Second test sentence.",
            "Third test sentence."
        ]
        
        response = client.embeddings.create(
            model=model,
            input=texts,
            user=USER_ID,  # Track which user made this request
            extra_body={
                "metadata": {**USER_METADATA, "test_type": "batch_embedding", "batch_size": len(texts)}
            },
            extra_headers={
                "x-litellm-spend-logs-metadata": SPEND_LOGS_METADATA
            }
        )
        
        print("✅ SUCCESS!")
        print(f"Model: {response.model}")
        print(f"Number of embeddings: {len(response.data)}")
        print(f"Embedding dimensions: {len(response.data[0].embedding)}")
        print(f"Total tokens used: {response.usage.total_tokens}")
        
        return True
        
    except Exception as e:
        print(f"❌ FAILED!")
        print(f"Error: {str(e)}")
        return False


def fetch_available_models(client: OpenAI):
    """Fetch and display models available for the current key."""
    print(f"\n{'='*80}")
    print("Fetching Available Models")
    print(f"{'='*80}")
    
    try:
        models_response = client.models.list()
        available = [m.id for m in models_response.data]
        
        print(f"\n✅ Found {len(available)} model(s) available for your key:")
        for model in sorted(available):
            print(f"  - {model}")
        
        return available
        
    except Exception as e:
        print(f"❌ Failed to fetch models: {e}")
        return []


def main():
    """Main function to run all tests."""
    print("\n" + "="*80)
    print("LiteLLM Proxy Setup Test")
    print("="*80)
    print(f"\nProxy URL: {LITELLM_PROXY_URL}")
    print(f"Virtual Key: {VIRTUAL_KEY[:20]}...")
    print(f"User ID: {USER_ID}")
    print(f"Metadata: {USER_METADATA}")
    print()
    
    # Initialize OpenAI client pointing to LiteLLM proxy
    # Use http_client parameter to handle SSL properly
    # Create httpx client with proper SSL handling and headers
    http_client = httpx.Client(
        verify=True,  # Verify SSL certificates
        timeout=60.0,  # Set reasonable timeout
        headers={
            "User-Agent": "curl/8.5.0"  # Match curl's user agent to bypass potential nginx filtering
        }
    )
    
    client = OpenAI(
        api_key=VIRTUAL_KEY,
        base_url=LITELLM_PROXY_URL,
        http_client=http_client
    )
    
    # Fetch available models for this key
    available_models = fetch_available_models(client)
    
    if not available_models:
        print("\n❌ No models available. Check your key permissions.")
        return 1
    
    # Filter configured models to only those available
    chat_models = [m for m in CHAT_MODELS if m in available_models]
    embedding_models = [m for m in EMBEDDING_MODELS if m in available_models]
    
    skipped_chat = [m for m in CHAT_MODELS if m not in available_models]
    skipped_embedding = [m for m in EMBEDDING_MODELS if m not in available_models]
    
    if skipped_chat:
        print(f"\n⚠️  Skipping chat models (not available for key): {', '.join(skipped_chat)}")
    if skipped_embedding:
        print(f"⚠️  Skipping embedding models (not available for key): {', '.join(skipped_embedding)}")
    
    results = {
        "chat": {},
        "streaming": {},
        "embeddings": {},
        "batch_embeddings": {}
    }
    
    # Test chat models
    if chat_models:
        print("\n" + "="*80)
        print("PART 1: Testing Chat Completion Models")
        print("="*80)
        
        for model in chat_models:
            results["chat"][model] = test_chat_completion(client, model)
        
        # Test streaming with first chat model
        print("\n" + "="*80)
        print("PART 2: Testing Streaming")
        print("="*80)
        
        results["streaming"][chat_models[0]] = test_streaming_completion(client, chat_models[0])
    else:
        print("\n⏭️  No chat models available — skipping Parts 1 & 2")
    
    # Test embedding models
    if embedding_models:
        print("\n" + "="*80)
        print("PART 3: Testing Embedding Models")
        print("="*80)
        
        for model in embedding_models:
            results["embeddings"][model] = test_embedding(client, model)
        
        # Test batch embeddings with first embedding model
        print("\n" + "="*80)
        print("PART 4: Testing Batch Embeddings")
        print("="*80)
        
        results["batch_embeddings"][embedding_models[0]] = test_batch_embeddings(client, embedding_models[0])
    else:
        print("\n⏭️  No embedding models available — skipping Parts 3 & 4")
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    total_tests = 0
    passed_tests = 0
    
    print("\nChat Completion Tests:")
    for model, result in results["chat"].items():
        total_tests += 1
        if result:
            passed_tests += 1
            print(f"  ✅ {model}")
        else:
            print(f"  ❌ {model}")
    
    print("\nStreaming Tests:")
    for model, result in results["streaming"].items():
        total_tests += 1
        if result:
            passed_tests += 1
            print(f"  ✅ {model}")
        else:
            print(f"  ❌ {model}")
    
    print("\nEmbedding Tests:")
    for model, result in results["embeddings"].items():
        total_tests += 1
        if result:
            passed_tests += 1
            print(f"  ✅ {model}")
        else:
            print(f"  ❌ {model}")
    
    print("\nBatch Embedding Tests:")
    for model, result in results["batch_embeddings"].items():
        total_tests += 1
        if result:
            passed_tests += 1
            print(f"  ✅ {model}")
        else:
            print(f"  ❌ {model}")
    
    print("\n" + "="*80)
    print(f"FINAL RESULT: {passed_tests}/{total_tests} tests passed")
    print("="*80)
    
    if passed_tests == total_tests:
        print("\n🎉 ALL TESTS PASSED! Your LiteLLM setup is working perfectly!")
        print("\nNext steps:")
        print("  1. Start using these models in your applications")
        print("  2. Monitor usage in the LiteLLM dashboard: https://localhost:4000/ui")
        print("  3. Check spend and budgets for your team")
        return 0
    else:
        print(f"\n⚠️  Some tests failed. Please check the errors above.")
        print("\nTroubleshooting:")
        print("  1. Verify LiteLLM proxy is running: curl https://localhost:4000/health")
        print("  2. Check that models are configured correctly in the dashboard")
        print("  3. Verify your virtual key has access to these models")
        print("  4. Check LiteLLM logs for detailed error messages")
        return 1


if __name__ == "__main__":
    sys.exit(main())
