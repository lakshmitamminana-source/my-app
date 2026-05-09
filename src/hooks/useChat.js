/**
 * Hook for managing chat threads and messages.
 */
import { useCallback, useState } from 'react'

const API_BASE = '/api/chat'

function formatAttachmentForApi(attachment) {
  // For API, only include a reference to the attachment, not the full content
  // This keeps the message under the 8000 character limit
  const name = attachment.name || 'attachment'
  const type = attachment.type || 'unknown'
  const size = attachment.textContent?.length || 0
  
  switch (type) {
    case 'image':
    case 'video':
      return `[${type.toUpperCase()} attachment: ${name}]`
    case 'table':
    case 'code':
    case 'formula':
    case 'pdf':
      // For text-based attachments, include a reference with size info
      return `[${type.toUpperCase()} attachment: ${name} (~${Math.round(size / 1024)}KB)]`
    default:
      return `[Attachment: ${name}]`
  }
}

function normalizeAttachmentForApi(attachment) {
  let textContent = attachment.textContent || attachment.text_content || ''
  
  // Ensure textContent is a string
  if (typeof textContent !== 'string') {
    if (typeof textContent === 'object' && textContent !== null) {
      try {
        textContent = JSON.stringify(textContent)
      } catch (e) {
        textContent = String(textContent)
      }
    } else {
      textContent = String(textContent || '')
    }
  }
  
  return {
    type: attachment.type,
    name: attachment.name,
    mime_type: attachment.mimeType || attachment.mime_type,
    text_content: textContent,
    data_url: attachment.dataUrl || attachment.data_url,
    language: attachment.language,
  }
}

function buildUserMessageForApi(message, attachments = []) {
  // For API: only include attachment references, not full content
  // This keeps the message under the 8000 character backend limit
  if (!attachments.length) return message
  const attachmentText = attachments.map(formatAttachmentForApi).join(' ')
  return `${message} ${attachmentText}`
}

function normalizeHistoryForApi(history = []) {
  // Strip large attachment content from history messages
  // Each message's content must be < 8000 chars for API validation
  const MAX_CONTENT_LENGTH = 7500 // Leave buffer for attachment references
  
  return history.map(turn => {
    if (!turn.content) return turn
    
    // If content is already under limit, send as-is
    if (turn.content.length <= MAX_CONTENT_LENGTH) {
      return turn
    }
    
    // Content is too long - likely has embedded attachment content
    // Try to extract just the message without the attachment block
    const lines = turn.content.split('\n')
    
    // Find where attachment markers start (like "### Code", "### Table", etc)
    let cutoffIndex = lines.length
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].match(/^#{1,3}\s+(Code|Table|Image|Video|Formula|Attachment)/)) {
        cutoffIndex = i
        break
      }
    }
    
    let truncatedContent = lines.slice(0, cutoffIndex).join('\n').trim()
    
    // If still too long, truncate with ellipsis
    if (truncatedContent.length > MAX_CONTENT_LENGTH) {
      truncatedContent = truncatedContent.substring(0, MAX_CONTENT_LENGTH - 20) + '...(truncated)'
    }
    
    return {
      ...turn,
      content: truncatedContent || '(previous message with large attachment)'
    }
  })
}

export function useChat() {
  const [threads, setThreads] = useState([])
  const [activeThreadId, setActiveThreadId] = useState(null)
  const [messages, setMessages] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')

  // Load all threads for current user
  const loadThreads = useCallback(async () => {
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/threads`, {
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to load threads')

      const data = await response.json()
      setThreads(data)
      return data
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load threads'
      setError(msg)
      return []
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Load a specific thread with all messages
  const loadThread = useCallback(async (threadId) => {
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/threads/${threadId}`, {
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to load thread')

      const thread = await response.json()
      setActiveThreadId(threadId)
      setMessages(thread.messages || [])
      return thread
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load thread'
      setError(msg)
      return null
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Create a new thread
  const createThread = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/threads`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to create thread')

      const thread = await response.json()
      setThreads((prev) => [thread, ...prev])
      setActiveThreadId(thread.id)
      setMessages([])
      return thread
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to create thread'
      setError(msg)
      throw err
    }
  }, [])

  // Send message and get response
  const sendMessage = useCallback(
    async (message, history = [], attachments = []) => {
      if (!activeThreadId) throw new Error('No active thread')
      
      console.log('=== SEND MESSAGE ===')
      console.log('Attachments before normalization:', attachments.map(a => ({
        name: a.name,
        type: a.type,
        textContentType: typeof a.textContent,
        textContentLength: a.textContent?.length
      })))
      
      const attachmentPayload = attachments.map(normalizeAttachmentForApi)
      
      console.log('Attachments after normalization:', attachmentPayload.map(a => ({
        name: a.name,
        type: a.type,
        text_content_type: typeof a.text_content,
        text_content_length: a.text_content?.length
      })))

      setIsLoading(true)
      setError('')

      try {
        // Build message content for API (with attachment references only, not full content)
        const messageForApi = buildUserMessageForApi(message, attachments)
        
        // Normalize history to ensure no message exceeds 8000 char limit
        const normalizedHistory = normalizeHistoryForApi(history)
        
        const response = await fetch(`${API_BASE}/threads/${activeThreadId}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            message: messageForApi,
            history: normalizedHistory,
            attachments: attachmentPayload,
          }),
        })

        if (!response.ok) {
          const data = await response.json()
          throw new Error(data.detail || 'Failed to send message')
        }

        const result = await response.json()

        if (result.thread_title) {
          setThreads((prev) =>
            prev.map((thread) =>
              thread.id === activeThreadId
                ? { ...thread, title: result.thread_title }
                : thread
            )
          )
        }

        // Add messages to state
        // Show only compact attachment references in the chat bubble.
        // Full attachment text is still sent in `attachmentPayload` for answering.
        const newMessage = {
          role: 'user',
          content: buildUserMessageForApi(message, attachments),
          attachments: [],
        }
        
        console.log('Message added to state:', {
          role: newMessage.role,
          attachments: newMessage.attachments?.map(a => ({
            name: a.name,
            type: a.type,
            textContentType: typeof a.textContent
          }))
        })
        
        setMessages((prev) => [
          ...prev,
          newMessage,
          {
            role: 'assistant',
            content: result.answer,
            attachments: result.assistant_attachments || [],
          },
        ])

        return result.answer
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to send message'
        setError(msg)
        throw err
      } finally {
        setIsLoading(false)
      }
    },
    [activeThreadId]
  )

  // Delete a thread
  const deleteThread = useCallback(async (threadId) => {
    try {
      const response = await fetch(`${API_BASE}/threads/${threadId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to delete thread')

      setThreads((prev) => prev.filter((t) => t.id !== threadId))
      if (activeThreadId === threadId) {
        setActiveThreadId(null)
        setMessages([])
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to delete thread'
      setError(msg)
    }
  }, [activeThreadId])

  // Update thread (e.g., rename)
  const updateThread = useCallback(async (threadId, updates) => {
    try {
      const response = await fetch(`${API_BASE}/threads/${threadId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(updates),
      })
      if (!response.ok) throw new Error('Failed to update thread')

      const updatedThread = await response.json()
      setThreads((prev) =>
        prev.map((t) => (t.id === threadId ? updatedThread : t))
      )
      return updatedThread
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to update thread'
      setError(msg)
      throw err
    }
  }, [])

  return {
    threads,
    activeThreadId,
    messages,
    isLoading,
    error,
    loadThreads,
    loadThread,
    createThread,
    sendMessage,
    deleteThread,
    updateThread,
    setError,
  }
}
