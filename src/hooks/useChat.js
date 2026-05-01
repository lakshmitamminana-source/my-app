/**
 * Hook for managing chat threads and messages.
 */
import { useCallback, useState } from 'react'

const API_BASE = '/api/chat'

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
    async (message, history = []) => {
      if (!activeThreadId) throw new Error('No active thread')

      setIsLoading(true)
      setError('')

      try {
        const response = await fetch(`${API_BASE}/threads/${activeThreadId}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            message,
            history,
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
        setMessages((prev) => [
          ...prev,
          { role: 'user', content: message },
          { role: 'assistant', content: result.answer },
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
