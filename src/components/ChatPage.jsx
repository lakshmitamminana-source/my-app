/**
 * Main chat interface with thread management.
 */
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useChat } from '../hooks/useChat'

export default function ChatPage({ user, onLogout }) {
  const markdownUrlTransform = (url) => {
    if (typeof url === 'string' && url.startsWith('data:image/')) {
      return url
    }
    return defaultUrlTransform(url)
  }

  const {
    threads,
    activeThreadId,
    messages,
    isLoading,
    error,
    loadThreads,
    loadThread,
    createThread,
    sendMessage,
    saveDbConnection,
    listDbConnections,
    queryDatabase,
    loadGoogleSheet,
    askGoogleSheetQuestion,
    uploadLocalFile,
    queryLocalFile,
    startResearchDigestStream,
    exportResearchDigestPdf,
    deleteThread,
    updateThread,
    setError,
  } = useChat()

  const [input, setInput] = useState('')
  const [showSidebar, setShowSidebar] = useState(true)
  const [isCreatingThread, setIsCreatingThread] = useState(false)
  const [editingThreadId, setEditingThreadId] = useState(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [isSavingTitle, setIsSavingTitle] = useState(false)
  const [activeTool, setActiveTool] = useState(null)
  const [dbUrl, setDbUrl] = useState('')
  const [dbConnectionLabel, setDbConnectionLabel] = useState('')
  const [dbConnections, setDbConnections] = useState([])
  const [selectedConnectionId, setSelectedConnectionId] = useState('')
  const [dbQuestion, setDbQuestion] = useState('')
  const [dbQueryResult, setDbQueryResult] = useState(null)
  const [isDbQueryLoading, setIsDbQueryLoading] = useState(false)
  const [isSavingDbConnection, setIsSavingDbConnection] = useState(false)
  const [isLoadingDbConnections, setIsLoadingDbConnections] = useState(false)
  const [sheetUrlOrId, setSheetUrlOrId] = useState('')
  const [sheetQuestion, setSheetQuestion] = useState('')
  const [sheetAnswer, setSheetAnswer] = useState('')
  const [sheetPreview, setSheetPreview] = useState(null)
  const [isSheetLoading, setIsSheetLoading] = useState(false)
  const [isSheetQuestionLoading, setIsSheetQuestionLoading] = useState(false)
  const [sheetTab, setSheetTab] = useState('google') // 'google' | 'local'
  const [localFile, setLocalFile] = useState(null) // { path, filename, source_type, row_count, columns, rows }
  const [isLocalFileUploading, setIsLocalFileUploading] = useState(false)
  const [localFileQuestion, setLocalFileQuestion] = useState('')
  const [localFileAnswer, setLocalFileAnswer] = useState('')
  const [isLocalFileQuerying, setIsLocalFileQuerying] = useState(false)
  const [researchTopic, setResearchTopic] = useState('')
  const [isResearchRunning, setIsResearchRunning] = useState(false)
  const [researchProgress, setResearchProgress] = useState([])
  const [researchDigest, setResearchDigest] = useState(null)
  const [isExportingResearchPdf, setIsExportingResearchPdf] = useState(false)
  const [isExportingResearchBibtex, setIsExportingResearchBibtex] = useState(false)
  const localFileInputRef = useRef(null)
  const researchEventSourceRef = useRef(null)
  const [pendingAttachments, setPendingAttachments] = useState([])
  const [isReadingAttachments, setIsReadingAttachments] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const messagesEndRef = useRef(null)
  const attachmentInputRef = useRef(null)
  const dragDepthRef = useRef(0)

  const codeExtensions = new Set([
    'js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'cs', 'go', 'rs', 'php', 'rb', 'swift',
    'kt', 'sql', 'sh', 'bash', 'yml', 'yaml', 'json', 'xml', 'html', 'css', 'scss', 'md', 'txt',
  ])
  const tableExtensions = new Set(['csv', 'tsv'])

  const readFileAsDataUrl = (file) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        // Ensure result is always a string
        const result = reader.result
        if (typeof result === 'string') {
          resolve(result)
        } else {
          reject(new Error('Failed to read file as data URL'))
        }
      }
      reader.onerror = () => reject(new Error(`Could not read file: ${file.name}`))
      reader.readAsDataURL(file)
    })

  const readFileAsText = (file) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        try {
          // Ensure result is always a string, not an object
          let text = reader.result
          console.log(`FileReader result type for ${file.name}:`, typeof text)
          
          if (typeof text !== 'string') {
            // Result is not a string - try to convert
            console.warn(`${file.name} FileReader result is ${typeof text}, converting...`)
            text = text ? String(text) : ''
          }
          
          // Additional safeguard: if somehow result object has toString() that returns [object Object]
          if (text === '[object Object]') {
            reject(new Error(`File ${file.name} could not be read properly - got [object Object]`))
            return
          }
          
          // Check if result is empty
          if (text === '' || text === null || text === undefined) {
            reject(new Error(`File ${file.name} is empty or could not be read`))
            return
          }
          
          console.log(`✓ Successfully read ${file.name}: ${text.length} characters`)
          resolve(text)
        } catch (err) {
          reject(new Error(`Error processing ${file.name}: ${err.message}`))
        }
      }
      reader.onerror = () => {
        console.error(`FileReader error for ${file.name}:`, reader.error)
        reject(new Error(`Could not read file: ${file.name}`))
      }
      reader.onabort = () => {
        reject(new Error(`Reading ${file.name} was aborted`))
      }
      reader.readAsText(file)
    })

  const buildAttachmentFromFile = async (file) => {
    const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : ''
    const base = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      name: file.name,
      mimeType: file.type || 'application/octet-stream',
    }

    if (file.type.startsWith('image/')) {
      const dataUrl = await readFileAsDataUrl(file)
      return { ...base, type: 'image', dataUrl }
    }

    if (file.type.startsWith('video/')) {
      const dataUrl = await readFileAsDataUrl(file)
      return { ...base, type: 'video', dataUrl }
    }

    // Helper to ensure textContent is valid
    const MAX_TEXT_CONTENT = 140000
    const validateTextContent = (text, filename) => {
      if (typeof text !== 'string') {
        throw new Error(`${filename}: textContent must be string, got ${typeof text}`)
      }
      if (!text || text.trim() === '') {
        throw new Error(`${filename}: textContent is empty`)
      }
      if (text.length > MAX_TEXT_CONTENT) {
        return text.slice(0, MAX_TEXT_CONTENT) + `\n\n[Content truncated — showing first ${MAX_TEXT_CONTENT.toLocaleString()} of ${text.length.toLocaleString()} characters]`
      }
      return text
    }

    // Handle table/spreadsheet files
    if (tableExtensions.has(ext) || ext === 'xls' || ext === 'xlsx') {
      const textContent = await readFileAsText(file)
      return { ...base, type: 'table', textContent: validateTextContent(textContent, file.name) }
    }

    // Handle text, code, and other documents
    if (file.type.startsWith('text/') || codeExtensions.has(ext) || ext === 'txt') {
      const textContent = await readFileAsText(file)
      return {
        ...base,
        type: 'code',
        language: ext || 'text',
        textContent: validateTextContent(textContent, file.name),
      }
    }

    // Handle PDF documents - extract text on backend
    if (ext === 'pdf' || file.type.includes('pdf')) {
      try {
        // Send PDF to backend for text extraction
        const formData = new FormData()
        formData.append('file', file)
        
        const response = await fetch('/api/chat/extract-pdf', {
          method: 'POST',
          credentials: 'include',
          body: formData,
        })
        
        if (!response.ok) {
          throw new Error(`PDF extraction failed: ${response.statusText}`)
        }
        
        const result = await response.json()
        return {
          ...base,
          type: 'pdf',
          language: 'pdf',
          textContent: validateTextContent(result.text, file.name),
        }
      } catch (err) {
        console.error('PDF extraction error:', err)
        throw new Error(`Could not extract PDF ${file.name}: ${err.message}`)
      }
    }

    // Fallback: treat unknown files as code/document attachments
    if (file.size < 5 * 1024 * 1024) {
      // If file is < 5MB, try to read as text
      try {
        const textContent = await readFileAsText(file)
        return {
          ...base,
          type: 'code',
          language: ext || 'text',
          textContent: validateTextContent(textContent, file.name),
        }
      } catch (textErr) {
        // If text reading fails, try data URL
        try {
          const dataUrl = await readFileAsDataUrl(file)
          const textContent = `[${file.name}] Binary file - ${(file.size / 1024).toFixed(2)}KB`
          return {
            ...base,
            type: 'code',
            language: ext || 'binary',
            textContent: validateTextContent(textContent, file.name),
            dataUrl,
          }
        } catch (dataErr) {
          throw new Error(`Could not read file ${file.name}: ${dataErr.message}`)
        }
      }
    }

    throw new Error(`File too large: ${file.name}. Maximum size is 5MB.`)
  }

  const handleAddFiles = async (fileList) => {
    const files = Array.from(fileList || [])
    if (!files.length) return

    setIsReadingAttachments(true)
    const nextAttachments = []
    const failedFiles = []

    for (const file of files) {
      try {
        console.log(`Reading file: ${file.name}, type: ${file.type}, size: ${file.size}`)
        const attachment = await buildAttachmentFromFile(file)
        console.log(`✓ Attachment created:`, { 
          name: attachment.name, 
          type: attachment.type, 
          textContentType: typeof attachment.textContent,
          hasTextContent: !!attachment.textContent
        })
        nextAttachments.push(attachment)
      } catch (err) {
        console.error(`✗ Failed to read ${file.name}:`, err.message)
        failedFiles.push(`${file.name} (${err.message})`)
      }
    }

    if (nextAttachments.length) {
      setPendingAttachments((prev) => [...prev, ...nextAttachments])
    }

    if (failedFiles.length) {
      setError(`Some files were skipped: ${failedFiles.join(', ')}`)
    }

    setIsReadingAttachments(false)
  }

  const handleAttachmentInputChange = async (e) => {
    await handleAddFiles(e.target.files)
    e.target.value = ''
  }

  const handlePasteIntoComposer = async (e) => {
    const clipboardItems = Array.from(e.clipboardData?.items || [])
    const pastedFiles = clipboardItems
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter(Boolean)

    if (!pastedFiles.length) {
      return
    }

    e.preventDefault()
    await handleAddFiles(pastedFiles)
  }

  const handleComposerDragEnter = (e) => {
    if (!activeThreadId || isLoading) return
    if (!e.dataTransfer?.types?.includes('Files')) return

    e.preventDefault()
    dragDepthRef.current += 1
    setIsDragActive(true)
  }

  const handleComposerDragOver = (e) => {
    if (!activeThreadId || isLoading) return
    if (!e.dataTransfer?.types?.includes('Files')) return

    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const handleComposerDragLeave = (e) => {
    if (!activeThreadId || isLoading) return
    if (!e.dataTransfer?.types?.includes('Files')) return

    e.preventDefault()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) {
      setIsDragActive(false)
    }
  }

  const handleComposerDrop = async (e) => {
    if (!activeThreadId || isLoading) return

    e.preventDefault()
    dragDepthRef.current = 0
    setIsDragActive(false)

    const droppedFiles = Array.from(e.dataTransfer?.files || [])
    if (!droppedFiles.length) return

    await handleAddFiles(droppedFiles)
  }

  const handleRemoveAttachment = (attachmentId) => {
    setPendingAttachments((prev) => prev.filter((attachment) => attachment.id !== attachmentId))
  }

  // Load threads on mount
  useEffect(() => {
    loadThreads()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    return () => {
      if (researchEventSourceRef.current) {
        researchEventSourceRef.current.close()
        researchEventSourceRef.current = null
      }
    }
  }, [])

  const handleNewChat = async () => {
    setError('')
    setIsCreatingThread(true)
    try {
      await createThread()
    } catch {
      setError('Failed to create new chat')
    } finally {
      setIsCreatingThread(false)
    }
  }

  const handleSelectThread = (threadId) => {
    loadThread(threadId)
    if (activeTool === 'query-db') {
      loadSavedConnections()
    }
  }

  const handleDeleteThread = async (e, threadId) => {
    e.stopPropagation()
    if (window.confirm('Delete this chat?')) {
      await deleteThread(threadId)
    }
  }

  const handleStartEditTitle = (e, threadId, currentTitle) => {
    e.stopPropagation()
    setEditingThreadId(threadId)
    setEditingTitle(currentTitle)
  }

  const handleCancelEditTitle = () => {
    setEditingThreadId(null)
    setEditingTitle('')
  }

  const handleSaveEditTitle = async (e, threadId) => {
    e.stopPropagation()
    const nextTitle = editingTitle.trim()

    if (!nextTitle) {
      setError('Title cannot be empty')
      return
    }

    setIsSavingTitle(true)
    try {
      await updateThread(threadId, { title: nextTitle })
      handleCancelEditTitle()
    } catch {
      setError('Failed to update chat title')
    } finally {
      setIsSavingTitle(false)
    }
  }

  const handleSendMessage = async (e) => {
    e.preventDefault()
    if ((!input.trim() && pendingAttachments.length === 0) || isLoading || !activeThreadId) return

    const userMessage = input.trim() || 'Shared attachments'
    const outgoingAttachments = [...pendingAttachments]
    setInput('')
    setPendingAttachments([])

    try {
      // Convert messages to proper format for API
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }))

        await sendMessage(userMessage, history, outgoingAttachments)
    } catch (err) {
      setPendingAttachments(outgoingAttachments)
      setError(err instanceof Error ? err.message : 'Failed to send message')
    }
  }

  const handleLogout = async () => {
    if (window.confirm('Are you sure you want to logout?')) {
      await onLogout()
    }
  }

  const handleOpenQueryDbTool = () => {
    if (researchEventSourceRef.current) {
      researchEventSourceRef.current.close()
      researchEventSourceRef.current = null
      setIsResearchRunning(false)
    }
    setActiveTool('query-db')
    setError('')
    loadSavedConnections()
  }

  const handleOpenGoogleSheetsTool = () => {
    if (researchEventSourceRef.current) {
      researchEventSourceRef.current.close()
      researchEventSourceRef.current = null
      setIsResearchRunning(false)
    }
    setActiveTool('google-sheets')
    setError('')
  }

  const handleOpenResearchDigestTool = () => {
    setActiveTool('research-digest')
    setError('')
  }

  const handleCloseToolPanel = () => {
    if (researchEventSourceRef.current) {
      researchEventSourceRef.current.close()
      researchEventSourceRef.current = null
      setIsResearchRunning(false)
    }
    setActiveTool(null)
  }

  const handleStartResearchDigest = (e) => {
    e.preventDefault()
    if (!researchTopic.trim()) {
      setError('Research topic is required.')
      return
    }

    if (researchEventSourceRef.current) {
      researchEventSourceRef.current.close()
      researchEventSourceRef.current = null
    }

    setError('')
    setResearchDigest(null)
    setResearchProgress([])
    setIsResearchRunning(true)

    const source = startResearchDigestStream({
      topic: researchTopic.trim(),
      maxIterations: 3,
      papersPerIteration: 4,
      onProgress: (event) => {
        setResearchProgress((prev) => [...prev, event])
      },
      onComplete: (digest) => {
        setResearchDigest(digest)
        setIsResearchRunning(false)
        researchEventSourceRef.current = null
      },
      onError: (message) => {
        setError(message || 'Research digest failed')
        setIsResearchRunning(false)
        researchEventSourceRef.current = null
      },
    })

    researchEventSourceRef.current = source
  }

  const handleStopResearchDigest = () => {
    if (researchEventSourceRef.current) {
      researchEventSourceRef.current.close()
      researchEventSourceRef.current = null
    }
    setIsResearchRunning(false)
  }

  const handleExportResearchPdf = async () => {
    if (!researchDigest) return

    setIsExportingResearchPdf(true)
    setError('')
    try {
      const blob = await exportResearchDigestPdf(researchDigest)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `research_digest_${Date.now()}.pdf`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      // Error text is already set by hook
    } finally {
      setIsExportingResearchPdf(false)
    }
  }

  const handleExportResearchBibtex = () => {
    if (!researchDigest?.citations?.length) return
    setIsExportingResearchBibtex(true)
    try {
      const bibtex = researchDigest.citations
        .map((citation) => citation.bibtex_entry || '')
        .filter(Boolean)
        .join('\n\n')

      if (!bibtex.trim()) {
        setError('No BibTeX entries available for export.')
        return
      }

      const blob = new Blob([bibtex], { type: 'application/x-bibtex;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `research_digest_${Date.now()}.bib`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } finally {
      setIsExportingResearchBibtex(false)
    }
  }

  const handleSubmitDbQuery = async (e) => {
    e.preventDefault()
    if (!dbQuestion.trim()) {
      setError('Question is required for Query DB.')
      return
    }
    if (!selectedConnectionId && !dbUrl.trim()) {
      setError('Select a saved connection or provide a database URL.')
      return
    }

    setIsDbQueryLoading(true)
    setError('')
    try {
      const result = await queryDatabase({
        connectionId: selectedConnectionId || null,
        databaseUrl: selectedConnectionId ? null : dbUrl.trim(),
        question: dbQuestion.trim(),
      })
      setDbQueryResult(result)
    } catch {
      setDbQueryResult(null)
    } finally {
      setIsDbQueryLoading(false)
    }
  }

  const loadSavedConnections = async () => {
    setIsLoadingDbConnections(true)
    try {
      const items = await listDbConnections(activeThreadId || null)
      setDbConnections(items)
    } catch {
      setDbConnections([])
    } finally {
      setIsLoadingDbConnections(false)
    }
  }

  const handleSaveDbConnection = async (e) => {
    e.preventDefault()
    if (!dbConnectionLabel.trim() || !dbUrl.trim()) {
      setError('Connection name and database URL are required to save.')
      return
    }

    setIsSavingDbConnection(true)
    setError('')
    try {
      const saved = await saveDbConnection({
        label: dbConnectionLabel.trim(),
        databaseUrl: dbUrl.trim(),
        threadId: activeThreadId || null,
      })
      setDbConnections((prev) => [saved, ...prev.filter((item) => item.id !== saved.id)])
      setSelectedConnectionId(saved.id)
      setDbConnectionLabel('')
      setDbUrl('')
    } finally {
      setIsSavingDbConnection(false)
    }
  }

  const handleLoadGoogleSheet = async (e) => {
    e.preventDefault()
    if (!sheetUrlOrId.trim()) {
      setError('Google Sheet URL or ID is required.')
      return
    }

    setIsSheetLoading(true)
    setError('')
    try {
      const preview = await loadGoogleSheet(sheetUrlOrId.trim())
      setSheetPreview(preview)
      setSheetAnswer('')
    } catch {
      setSheetPreview(null)
    } finally {
      setIsSheetLoading(false)
    }
  }

  const handleAskGoogleSheet = async (e) => {
    e.preventDefault()
    if (!sheetUrlOrId.trim()) {
      setError('Load a Google Sheet first by URL or ID.')
      return
    }
    if (!sheetQuestion.trim()) {
      setError('Question is required for Google Sheets tool.')
      return
    }

    setIsSheetQuestionLoading(true)
    setError('')
    try {
      const result = await askGoogleSheetQuestion({
        sheetUrlOrId: sheetUrlOrId.trim(),
        question: sheetQuestion.trim(),
      })
      setSheetAnswer(result.answer || '')
      setSheetPreview({
        row_count: result.row_count,
        columns: result.columns || [],
        rows: result.rows || [],
      })
    } catch {
      setSheetAnswer('')
    } finally {
      setIsSheetQuestionLoading(false)
    }
  }

  const handleBrowseLocalFile = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setIsLocalFileUploading(true)
    setLocalFile(null)
    setLocalFileAnswer('')
    setError('')
    uploadLocalFile(file)
      .then((result) => setLocalFile(result))
      .catch(() => setLocalFile(null))
      .finally(() => setIsLocalFileUploading(false))
  }

  const handleAskLocalFile = async (e) => {
    e.preventDefault()
    if (!localFile) {
      setError('Upload a file first using the Browse button.')
      return
    }
    if (!localFileQuestion.trim()) {
      setError('Question is required.')
      return
    }
    setIsLocalFileQuerying(true)
    setError('')
    try {
      const result = await queryLocalFile({
        filePath: localFile.path,
        sourceType: localFile.source_type,
        question: localFileQuestion.trim(),
      })
      setLocalFileAnswer(result.answer || '')
      setLocalFile((prev) => ({ ...prev, rows: result.rows || [], columns: result.columns || [], row_count: result.row_count }))
    } catch {
      setLocalFileAnswer('')
    } finally {
      setIsLocalFileQuerying(false)
    }
  }

  const parseCSV = (csvText) => {
    // Handle both string and object inputs
    let text = csvText
    if (typeof csvText === 'object' && csvText !== null) {
      try {
        text = JSON.stringify(csvText, null, 2)
      } catch (e) {
        text = String(csvText)
      }
    } else {
      text = String(csvText || '')
    }
    
    const lines = text.split('\n').filter((line) => line.trim())
    if (lines.length === 0) return { headers: [], rows: [] }

    // Simple CSV parser (handles basic cases, not all edge cases)
    const rows = lines.map((line) =>
      line.split(',').map((cell) => cell.trim())
    )

    return {
      headers: rows[0] || [],
      rows: rows.slice(1),
    }
  }

  const latestResearchProgress = researchProgress.length ? researchProgress[researchProgress.length - 1] : null
  const researchProgressPercent = Math.max(
    0,
    Math.min(100, Math.round(((latestResearchProgress?.progress ?? (isResearchRunning ? 0.05 : 0)) * 100)))
  )

  const renderAttachmentPreview = (attachment) => {
    try {
      const dataUrl = attachment.dataUrl || attachment.data_url
      const mimeType = attachment.mimeType || attachment.mime_type
      let textContent = attachment.textContent || attachment.text_content
      
      // Debug logging for [object Object] issues
      console.log(`Rendering attachment ${attachment.name} (type=${attachment.type}):`, {
        textContentType: typeof textContent,
        textContentValue: textContent ? String(textContent).substring(0, 50) : 'EMPTY',
        hasTextContent: !!textContent,
      })
      
      // Ensure textContent is always a string (handle object responses from API)
      if (textContent && typeof textContent !== 'string') {
        console.warn(`Converting non-string textContent for ${attachment.name}`)
        if (typeof textContent === 'object') {
          try {
            textContent = JSON.stringify(textContent, null, 2)
          } catch (e) {
            textContent = String(textContent)
          }
        } else {
          textContent = String(textContent)
        }
      } else if (!textContent) {
        textContent = ''
      }

      if (attachment.type === 'image' && dataUrl) {
        return <img src={dataUrl} alt={attachment.name || 'Image attachment'} className="attachment-image" />
      }

      if (attachment.type === 'video' && dataUrl) {
        return (
          <video className="attachment-video" controls preload="metadata">
            <source src={dataUrl} type={mimeType || 'video/mp4'} />
            Your browser does not support this video preview.
          </video>
        )
      }

      if (attachment.type === 'table') {
        const csvContent = String(textContent)
        const { headers, rows } = parseCSV(csvContent)
        
        // Check if parsing was successful
        const hasValidHeaders = headers.length > 0 && headers.some((h) => h.trim())
        
        if (!hasValidHeaders) {
          // Fallback to pre if parsing fails
          return (
            <pre className="attachment-code-block" style={{ maxHeight: '300px', overflow: 'auto' }}>
              {csvContent}
            </pre>
          )
        }
        
        return (
          <div className="attachment-table-container">
            <table className="attachment-table">
              <thead>
                <tr>
                  {headers.map((header, i) => (
                    <th key={i}>{header || `Col ${i}`}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows && rows.map((row, rowIdx) => (
                  <tr key={rowIdx}>
                    {row && row.map((cell, cellIdx) => (
                      <td key={cellIdx}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      }

      if (attachment.type === 'formula') {
        return <pre className="attachment-code-block">{`$$${String(textContent || '')}$$`}</pre>
      }

      if (attachment.type === 'code') {
        return (
          <pre className="attachment-code-block">
            <code>{String(textContent || '')}</code>
          </pre>
        )
      }

      if (attachment.type === 'pdf') {
        return (
          <pre className="attachment-code-block">
            <code>{String(textContent || '')}</code>
          </pre>
        )
      }

      console.warn(`Unknown attachment type: ${attachment.type}`)
      return null
    } catch (err) {
      console.error(`Error rendering attachment ${attachment?.name}:`, err)
      return (
        <div className="attachment-error">
          <p>Error displaying attachment: {err.message}</p>
          <p>Type: {attachment?.type}</p>
        </div>
      )
    }
  }

  return (
    <div className="chat-layout">
      {/* Sidebar */}
      <aside className={`sidebar ${showSidebar ? 'visible' : 'hidden'}`}>
        <div className="sidebar-header">
          <h2>Chats</h2>
          <button
            type="button"
            onClick={handleNewChat}
            disabled={isCreatingThread}
            className="new-chat-button"
            title="New Chat"
            aria-label="Create new chat"
          >
            {isCreatingThread ? '...' : '+'}
          </button>
        </div>

        <div className="threads-list">
          {threads.length === 0 ? (
            <p className="no-threads">No chats yet. Start a new one!</p>
          ) : (
            threads.map((thread) => (
              <div
                key={thread.id}
                className={`thread-item ${activeThreadId === thread.id ? 'active' : ''}`}
                onClick={() => handleSelectThread(thread.id)}
              >
                {editingThreadId === thread.id ? (
                  <>
                    <input
                      type="text"
                      className="thread-title-input"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          handleSaveEditTitle(e, thread.id)
                        }
                        if (e.key === 'Escape') {
                          handleCancelEditTitle()
                        }
                      }}
                      autoFocus
                    />
                    <button
                      className="thread-action-button"
                      onClick={(e) => handleSaveEditTitle(e, thread.id)}
                      title="Save title"
                      aria-label="Save title"
                      disabled={isSavingTitle}
                    >
                      ✓
                    </button>
                    <button
                      className="thread-action-button"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleCancelEditTitle()
                      }}
                      title="Cancel editing"
                      aria-label="Cancel editing"
                      disabled={isSavingTitle}
                    >
                      ✕
                    </button>
                  </>
                ) : (
                  <>
                    <div className="thread-title">{thread.title}</div>
                    <button
                      className="thread-action-button"
                      onClick={(e) => handleStartEditTitle(e, thread.id, thread.title)}
                      title="Edit title"
                      aria-label="Edit title"
                    >
                      ✎
                    </button>
                    <button
                      className="delete-button"
                      onClick={(e) => handleDeleteThread(e, thread.id)}
                      title="Delete chat"
                      aria-label="Delete chat"
                    >
                      <svg
                        className="delete-icon"
                        viewBox="0 0 24 24"
                        fill="none"
                        xmlns="http://www.w3.org/2000/svg"
                      >
                        <path
                          d="M3 6h18M8 6V4h8v2m-9 0l1 14h8l1-14M10 10v7m4-7v7"
                          stroke="currentColor"
                          strokeWidth="1.8"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </button>
                  </>
                )}
              </div>
            ))
          )}
        </div>

        <div className="sidebar-tools">
          <h3>Tools</h3>
          <button
            type="button"
            className={`tool-item ${activeTool === 'query-db' ? 'active' : ''}`}
            onClick={handleOpenQueryDbTool}
          >
            Query DB
          </button>
          <button
            type="button"
            className={`tool-item ${activeTool === 'google-sheets' ? 'active' : ''}`}
            onClick={handleOpenGoogleSheetsTool}
          >
            Google Sheets
          </button>
          <button
            type="button"
            className={`tool-item ${activeTool === 'research-digest' ? 'active' : ''}`}
            onClick={handleOpenResearchDigestTool}
          >
            Research Digest
          </button>
        </div>

      </aside>

      {/* Main Chat Area */}
      <main className="chat-main">
        <div className="chat-header">
          <button
            className="toggle-sidebar-button"
            onClick={() => setShowSidebar(!showSidebar)}
            title="Toggle sidebar"
          >
            ☰
          </button>
          <div className="header-title">
            <h1>Amzur AI Chat</h1>
          </div>
          <div className="header-user-block" title={user?.email || ''}>
            <span className="header-welcome">Welcome,</span>
            <span className="header-user-email">{user?.email}</span>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="logout-icon-button"
            title="Logout"
            aria-label="Logout"
          >
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path
                d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M14 16l4-4m0 0l-4-4m4 4H9"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>

        <div className="messages-container" role="log" aria-live="polite">
          {activeTool === 'query-db' ? (
            <section className="tool-panel" aria-label="Query DB tool panel">
              <div className="tool-panel-header">
                <h2>Query DB</h2>
                <button type="button" className="tool-close-button" onClick={handleCloseToolPanel}>
                  Close
                </button>
              </div>
              <p className="tool-panel-description">
                Connect to a database and ask questions in natural language. It supports questions that are typically asked as SQL statements.
              </p>
              <form className="tool-panel-form" onSubmit={handleSubmitDbQuery}>
                <label htmlFor="db-connection-select">Saved Connections</label>
                <div className="tool-inline-row">
                  <select
                    id="db-connection-select"
                    value={selectedConnectionId}
                    onChange={(e) => setSelectedConnectionId(e.target.value)}
                    disabled={isDbQueryLoading || isLoadingDbConnections}
                  >
                    <option value="">Use direct URL</option>
                    {dbConnections.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.label} ({connection.masked_url})
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="tool-secondary-button"
                    onClick={loadSavedConnections}
                    disabled={isLoadingDbConnections || isDbQueryLoading}
                  >
                    {isLoadingDbConnections ? 'Loading...' : 'Refresh'}
                  </button>
                </div>

                <label htmlFor="db-connection-label-input">Connection Name</label>
                <input
                  id="db-connection-label-input"
                  type="text"
                  placeholder="Example: Production Analytics"
                  value={dbConnectionLabel}
                  onChange={(e) => setDbConnectionLabel(e.target.value)}
                  disabled={isSavingDbConnection || isDbQueryLoading}
                />

                <label htmlFor="db-url-input">Database URL</label>
                <input
                  id="db-url-input"
                  type="text"
                  placeholder="postgresql+psycopg2://user:password@host:5432/dbname"
                  value={dbUrl}
                  onChange={(e) => setDbUrl(e.target.value)}
                  disabled={isDbQueryLoading || !!selectedConnectionId}
                />

                <button
                  type="button"
                  className="tool-secondary-button"
                  onClick={handleSaveDbConnection}
                  disabled={
                    isSavingDbConnection ||
                    isDbQueryLoading ||
                    !dbConnectionLabel.trim() ||
                    !dbUrl.trim()
                  }
                >
                  {isSavingDbConnection ? 'Saving...' : 'Save Connection'}
                </button>

                <label htmlFor="db-question-input">Question</label>
                <textarea
                  id="db-question-input"
                  rows={3}
                  placeholder="Example: What are the top 5 threads created this week?"
                  value={dbQuestion}
                  onChange={(e) => setDbQuestion(e.target.value)}
                  disabled={isDbQueryLoading}
                />

                <button type="submit" disabled={isDbQueryLoading} className="tool-submit-button">
                  {isDbQueryLoading ? 'Running...' : 'Run Query'}
                </button>
              </form>

              {dbQueryResult ? (
                <div className="tool-result">
                  <h3>Answer</h3>
                  <div className="tool-answer-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={markdownUrlTransform}>{dbQueryResult.answer}</ReactMarkdown>
                  </div>
                  <h3>Generated SQL</h3>
                  <pre>{dbQueryResult.sql}</pre>
                  <h3>Rows ({dbQueryResult.row_count})</h3>
                  {Array.isArray(dbQueryResult.rows) && dbQueryResult.rows.length > 0 ? (
                    <div className="tool-table-wrapper">
                      <table className="tool-table-preview">
                        <thead>
                          <tr>
                            {(dbQueryResult.columns || []).map((column) => (
                              <th key={column}>{column}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {dbQueryResult.rows.slice(0, 20).map((row, rowIdx) => (
                            <tr key={rowIdx}>
                              {(dbQueryResult.columns || []).map((column) => (
                                <td key={`${rowIdx}-${column}`}>{String(row?.[column] ?? '')}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p>No rows returned.</p>
                  )}
                </div>
              ) : null}
            </section>
          ) : null}

          {activeTool === 'google-sheets' ? (
            <section className="tool-panel" aria-label="Google Sheets tool panel">
              <div className="tool-panel-header">
                <h2>Google Sheets &amp; Local Files</h2>
                <button type="button" className="tool-close-button" onClick={handleCloseToolPanel}>
                  Close
                </button>
              </div>

              {/* Tab switcher */}
              <div className="tool-tabs">
                <button
                  type="button"
                  className={`tool-tab ${sheetTab === 'google' ? 'active' : ''}`}
                  onClick={() => { setSheetTab('google'); setError('') }}
                >
                  Google Sheet
                </button>
                <button
                  type="button"
                  className={`tool-tab ${sheetTab === 'local' ? 'active' : ''}`}
                  onClick={() => { setSheetTab('local'); setError('') }}
                >
                  Local File (CSV / XLSX)
                </button>
              </div>

              {/* ── Google Sheet tab ── */}
              {sheetTab === 'google' ? (
                <>
                  <p className="tool-panel-description">
                    Load a Google Sheet using its URL or spreadsheet ID and preview the tabular data.
                  </p>
                  <form className="tool-panel-form" onSubmit={handleLoadGoogleSheet}>
                    <label htmlFor="sheet-url-input">Sheet URL or ID</label>
                    <input
                      id="sheet-url-input"
                      type="text"
                      placeholder="https://docs.google.com/spreadsheets/d/.../edit"
                      value={sheetUrlOrId}
                      onChange={(e) => setSheetUrlOrId(e.target.value)}
                      disabled={isSheetLoading}
                    />
                    <button type="submit" disabled={isSheetLoading} className="tool-submit-button">
                      {isSheetLoading ? 'Loading...' : 'Load Sheet'}
                    </button>
                  </form>

                  <form className="tool-panel-form" onSubmit={handleAskGoogleSheet}>
                    <label htmlFor="sheet-question-input">Question</label>
                    <textarea
                      id="sheet-question-input"
                      rows={3}
                      placeholder="Example: How many rows have Status as Completed?"
                      value={sheetQuestion}
                      onChange={(e) => setSheetQuestion(e.target.value)}
                      disabled={isSheetQuestionLoading}
                    />
                    <button
                      type="submit"
                      disabled={isSheetQuestionLoading || !sheetUrlOrId.trim()}
                      className="tool-submit-button"
                    >
                      {isSheetQuestionLoading ? 'Asking...' : 'Ask Question'}
                    </button>
                  </form>

                  {sheetAnswer ? (
                    <div className="tool-result">
                      <h3>Answer</h3>
                      <div className="tool-answer-markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={markdownUrlTransform}>{sheetAnswer}</ReactMarkdown>
                      </div>
                    </div>
                  ) : null}

                  {sheetPreview ? (
                    <div className="tool-result">
                      <h3>Rows ({sheetPreview.row_count})</h3>
                      {Array.isArray(sheetPreview.rows) && sheetPreview.rows.length > 0 ? (
                        <div className="tool-table-wrapper">
                          <table className="tool-table-preview">
                            <thead>
                              <tr>
                                {(sheetPreview.columns || []).map((column) => (
                                  <th key={column}>{column}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {sheetPreview.rows.slice(0, 20).map((row, rowIdx) => (
                                <tr key={rowIdx}>
                                  {(sheetPreview.columns || []).map((column) => (
                                    <td key={`${rowIdx}-${column}`}>{String(row?.[column] ?? '')}</td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <p>No rows found in the sheet.</p>
                      )}
                    </div>
                  ) : null}
                </>
              ) : null}

              {/* ── Local File tab ── */}
              {sheetTab === 'local' ? (
                <>
                  <p className="tool-panel-description">
                    Upload a local CSV or Excel (.xlsx) file and ask questions about it.
                  </p>

                  {/* Hidden native file input */}
                  <input
                    ref={localFileInputRef}
                    type="file"
                    accept=".csv,.xlsx,.xls"
                    style={{ display: 'none' }}
                    onChange={handleBrowseLocalFile}
                  />

                  <div className="tool-panel-form">
                    <div className="local-file-browse-row">
                      <button
                        type="button"
                        className="tool-submit-button"
                        disabled={isLocalFileUploading}
                        onClick={() => localFileInputRef.current?.click()}
                      >
                        {isLocalFileUploading ? 'Uploading...' : 'Browse'}
                      </button>
                      {localFile ? (
                        <span className="local-file-name">
                          {localFile.filename} — {localFile.row_count.toLocaleString()} rows
                        </span>
                      ) : (
                        <span className="local-file-placeholder">No file selected</span>
                      )}
                    </div>
                  </div>

                  {localFile ? (
                    <>
                      {/* Preview table */}
                      {Array.isArray(localFile.rows) && localFile.rows.length > 0 ? (
                        <div className="tool-result">
                          <h3>Preview — {localFile.row_count.toLocaleString()} rows × {localFile.columns.length} columns</h3>
                          <div className="tool-table-wrapper">
                            <table className="tool-table-preview">
                              <thead>
                                <tr>
                                  {(localFile.columns || []).map((col) => (
                                    <th key={col}>{col}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {localFile.rows.slice(0, 20).map((row, rowIdx) => (
                                  <tr key={rowIdx}>
                                    {(localFile.columns || []).map((col) => (
                                      <td key={`${rowIdx}-${col}`}>{String(row?.[col] ?? '')}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ) : null}

                      {/* Question form */}
                      <form className="tool-panel-form" onSubmit={handleAskLocalFile}>
                        <label htmlFor="local-file-question">Question</label>
                        <textarea
                          id="local-file-question"
                          rows={3}
                          placeholder="Example: What is the average salary?"
                          value={localFileQuestion}
                          onChange={(e) => setLocalFileQuestion(e.target.value)}
                          disabled={isLocalFileQuerying}
                        />
                        <button
                          type="submit"
                          disabled={isLocalFileQuerying}
                          className="tool-submit-button"
                        >
                          {isLocalFileQuerying ? 'Asking...' : 'Ask Question'}
                        </button>
                      </form>

                      {localFileAnswer ? (
                        <div className="tool-result">
                          <h3>Answer</h3>
                          <div className="tool-answer-markdown">
                            <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={markdownUrlTransform}>{localFileAnswer}</ReactMarkdown>
                          </div>
                        </div>
                      ) : null}
                    </>
                  ) : null}
                </>
              ) : null}
            </section>
          ) : null}

          {activeTool === 'research-digest' ? (
            <section className="tool-panel" aria-label="Research Digest tool panel">
              <div className="tool-panel-header">
                <h2>Research Digest Agent</h2>
                <button type="button" className="tool-close-button" onClick={handleCloseToolPanel}>
                  Close
                </button>
              </div>
              <p className="tool-panel-description">
                Enter a topic and the agent will iteratively search arXiv, read paper sections, reflect on evidence sufficiency, and generate a structured digest.
              </p>

              <form className="tool-panel-form" onSubmit={handleStartResearchDigest}>
                <label htmlFor="research-topic-input">Research Topic</label>
                <textarea
                  id="research-topic-input"
                  rows={3}
                  placeholder="Example: Retrieval-Augmented Generation evaluation methods for enterprise QA"
                  value={researchTopic}
                  onChange={(e) => setResearchTopic(e.target.value)}
                  disabled={isResearchRunning}
                />
                <div className="tool-inline-row">
                  <button type="submit" disabled={isResearchRunning} className="tool-submit-button">
                    {isResearchRunning ? 'Running...' : 'Start Research'}
                  </button>
                  <button
                    type="button"
                    className="tool-secondary-button"
                    disabled={!isResearchRunning}
                    onClick={handleStopResearchDigest}
                  >
                    Stop
                  </button>
                  <button
                    type="button"
                    className="tool-secondary-button"
                    disabled={!researchDigest || isExportingResearchPdf}
                    onClick={handleExportResearchPdf}
                  >
                    {isExportingResearchPdf ? 'Exporting...' : 'Export PDF'}
                  </button>
                  <button
                    type="button"
                    className="tool-secondary-button"
                    disabled={!researchDigest || isExportingResearchBibtex}
                    onClick={handleExportResearchBibtex}
                  >
                    {isExportingResearchBibtex ? 'Exporting...' : 'Export BibTeX'}
                  </button>
                </div>
              </form>

              <div className="tool-result">
                <h3>Status</h3>
                {isResearchRunning ? (
                  <>
                    <p className="research-status-text">
                      Research is in progress. We are reviewing sources and preparing a simple, reader-friendly brief.
                    </p>
                    <div className="research-status-bar">
                      <div className="research-status-fill" style={{ width: `${researchProgressPercent}%` }} />
                    </div>
                    <p className="research-status-percent">{researchProgressPercent}%</p>
                    {researchProgress.length ? (
                      <div className="research-progress-list" aria-live="polite">
                        {researchProgress.slice(-10).map((event, idx) => (
                          <div className="research-progress-item" key={`${event.phase}-${idx}-${event.message}`}>
                            <div className="research-phase">{event.phase}</div>
                            <div>
                              <div>{event.message}</div>
                              {event.details && Object.keys(event.details).length ? (
                                <div className="research-progress-details">
                                  {Object.entries(event.details).map(([key, value]) => (
                                    <span className="research-progress-detail" key={`${key}-${String(value)}`}>
                                      <strong>{key}:</strong> {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p>Enter a topic and click Start Research to generate a plain-language digest.</p>
                )}
              </div>

              {researchDigest ? (
                <div className="tool-result">
                  <h3>News Brief</h3>
                  <div className="tool-answer-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={markdownUrlTransform}>
                      {researchDigest.executive_summary || ''}
                    </ReactMarkdown>
                  </div>

                  <h3>Key Takeaways</h3>
                  <ul className="research-list">
                    {(researchDigest.key_findings || []).map((item, idx) => (
                      <li key={`finding-${idx}`}>{item}</li>
                    ))}
                  </ul>

                  <h3>Confidence</h3>
                  <p>{researchDigest.evidence_assessment}</p>

                  <details className="research-details-block">
                    <summary>Sources and Technical Details</summary>

                    <p><strong>Method:</strong> {researchDigest.methodology_notes}</p>

                    <h3>Citations ({(researchDigest.citations || []).length})</h3>
                    {(researchDigest.citations || []).length ? (
                      <div className="tool-table-wrapper">
                        <table className="tool-table-preview">
                          <thead>
                            <tr>
                              <th>Citation</th>
                              <th>Title</th>
                              <th>Authors</th>
                              <th>Year</th>
                              <th>DOI</th>
                              <th>Published</th>
                              <th>arXiv ID</th>
                              <th>Relevance</th>
                              <th>PDF</th>
                            </tr>
                          </thead>
                          <tbody>
                            {researchDigest.citations.map((citation, idx) => (
                              <tr key={`citation-${idx}`}>
                                <td>{citation.citation_id || `C${idx + 1}`}</td>
                                <td>{citation.title}</td>
                                <td>{(citation.authors || []).join(', ')}</td>
                                <td>{citation.year || '-'}</td>
                                <td>{citation.doi || '-'}</td>
                                <td>{citation.published}</td>
                                <td>{citation.arxiv_id}</td>
                                <td>{citation.relevance_score ?? '-'}</td>
                                <td>
                                  <a href={citation.pdf_url} target="_blank" rel="noreferrer">
                                    Open PDF
                                  </a>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p>No citations were gathered.</p>
                    )}

                    <h3>References (APA)</h3>
                    <ol className="research-list">
                      {(researchDigest.citations || []).map((citation, idx) => (
                        <li key={`apa-${idx}`}>{citation.apa_citation || citation.title}</li>
                      ))}
                    </ol>

                    <h3>References (IEEE)</h3>
                    <ol className="research-list">
                      {(researchDigest.citations || []).map((citation, idx) => (
                        <li key={`ieee-${idx}`}>{citation.ieee_citation || citation.title}</li>
                      ))}
                    </ol>
                  </details>

                </div>
              ) : null}
            </section>
          ) : null}

          {error && <p className="error global-error">{error}</p>}
          {activeThreadId ? (
            <>
              {messages.length === 0 ? (
                <div className="empty-state">
                  <p>Start a conversation...</p>
                </div>
              ) : (
                messages.map((message, index) => (
                  <article key={`${message.role}-${index}`} className={`bubble ${message.role}`}>
                    <p className="role">{message.role === 'user' ? 'You' : 'Assistant'}</p>
                    <div className="content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={markdownUrlTransform}>{message.content}</ReactMarkdown>
                    </div>
                    {Array.isArray(message.attachments) && message.attachments.length > 0 ? (
                      <div className="message-attachments">
                        {message.attachments.map((attachment) => (
                          <div key={attachment.id || `${attachment.type}-${attachment.name}`} className="attachment-card">
                            <p className="attachment-label">{attachment.type.toUpperCase()}</p>
                            {renderAttachmentPreview(attachment)}
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </article>
                ))
              )}
              {isLoading && <p className="typing">Assistant is thinking...</p>}
            </>
          ) : (
            <div className="empty-state">
              <p>Create a new chat to get started</p>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <form
          className={`composer ${isDragActive ? 'drag-active' : ''}`}
          onSubmit={handleSendMessage}
          onDragEnter={handleComposerDragEnter}
          onDragOver={handleComposerDragOver}
          onDragLeave={handleComposerDragLeave}
          onDrop={handleComposerDrop}
        >
          <input
            ref={attachmentInputRef}
            type="file"
            multiple
            className="attachment-input"
            accept="image/*,video/*,.csv,.tsv,.txt,.pdf,.xls,.xlsx,.md,.json,.xml,.html,.css,.js,.jsx,.ts,.tsx,.py,.java,.c,.cpp,.cs,.go,.rs,.php,.rb,.swift,.kt,.sql,.sh,.yaml,.yml"
            onChange={handleAttachmentInputChange}
          />
          <button
            type="button"
            className="attach-files-icon-button"
            onClick={() => attachmentInputRef.current?.click()}
            disabled={isLoading || !activeThreadId || isReadingAttachments}
            title={isReadingAttachments ? 'Reading attachments...' : 'Attach files'}
            aria-label="Attach files"
          >
            +
          </button>

          {pendingAttachments.length > 0 ? (
            <div className="pending-attachments">
              {pendingAttachments.map((attachment) => (
                <div key={attachment.id} className="pending-attachment-chip">
                  <span>{attachment.type.toUpperCase()}: {attachment.name || 'inline-content'}</span>
                  <button
                    type="button"
                    className="remove-attachment-button"
                    onClick={() => handleRemoveAttachment(attachment.id)}
                    aria-label={`Remove ${attachment.name || attachment.type} attachment`}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <div className="composer-input-row">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onPaste={handlePasteIntoComposer}
              placeholder={activeThreadId ? 'Type your question...' : 'Create a new chat first'}
              rows={3}
              disabled={isLoading || !activeThreadId}
              className="composer-textarea"
            />
            <button
              type="submit"
              disabled={
                isLoading ||
                (!input.trim() && pendingAttachments.length === 0) ||
                !activeThreadId ||
                isReadingAttachments
              }
              className="composer-button"
            >
              {isLoading ? 'Sending...' : 'Send'}
            </button>
          </div>
          {isDragActive ? <p className="drop-help-text">Drop files here to attach</p> : null}
        </form>
      </main>
    </div>
  )
}
