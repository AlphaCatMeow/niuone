const INPUT_MODALITY_ATTRIBUTE = 'data-input-modality'

export function installInputModalityTracker(documentRef = document) {
  const root = documentRef.documentElement

  const handlePointerDown = () => {
    root.setAttribute(INPUT_MODALITY_ATTRIBUTE, 'pointer')
  }

  const handleKeyDown = (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return
    root.setAttribute(INPUT_MODALITY_ATTRIBUTE, 'keyboard')
  }

  documentRef.addEventListener('pointerdown', handlePointerDown, true)
  documentRef.addEventListener('keydown', handleKeyDown, true)

  return () => {
    documentRef.removeEventListener('pointerdown', handlePointerDown, true)
    documentRef.removeEventListener('keydown', handleKeyDown, true)
  }
}
