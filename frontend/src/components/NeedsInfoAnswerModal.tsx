/**
 * NeedsInfoAnswerModal — Operator answer modal for needs_info stories.
 * STORY-738: DB-Mediated Q&A — read question, submit answer, resume story.
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getQuestion, submitAnswer } from '../api/client';

interface NeedsInfoAnswerModalProps {
  storyId: string;
  repo?: string;
  isOpen: boolean;
  onClose: () => void;
  onAnswered: () => void;
}

export function NeedsInfoAnswerModal({
  storyId,
  repo,
  isOpen,
  onClose,
  onAnswered,
}: NeedsInfoAnswerModalProps) {
  const [answer, setAnswer] = useState('');
  const [alreadyAnswered, setAlreadyAnswered] = useState(false);
  const queryClient = useQueryClient();

  const questionQuery = useQuery({
    queryKey: ['dispatch-question', storyId, repo ?? ''],
    queryFn: () => getQuestion(storyId, repo),
    enabled: isOpen,
    retry: false,
  });

  const answerMutation = useMutation({
    mutationFn: () => submitAnswer(storyId, repo, answer),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dispatch-queue'] });
      onAnswered();
      onClose();
    },
    onError: (error: Error) => {
      if (error.message.includes('409')) {
        setAlreadyAnswered(true);
        queryClient.invalidateQueries({ queryKey: ['dispatch-queue'] });
        // Show "already answered" banner briefly before closing
        setTimeout(() => {
          onAnswered();
          onClose();
        }, 2500);
      }
    },
  });

  if (!isOpen) return null;

  const isSubmitting = answerMutation.isPending;
  const canSubmit = answer.trim().length > 0 && !isSubmitting;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-label={`Answer question for ${storyId}`}
    >
      <div className="bg-gray-800 border border-gray-600 rounded-lg shadow-xl w-full max-w-lg mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <div>
            <h2 className="text-gray-100 font-semibold text-sm">
              Answer Question &mdash; {storyId}
            </h2>
            {questionQuery.data && (
              <p className="text-gray-400 text-xs mt-0.5">
                Agent: {questionQuery.data.agent ?? 'unknown'}
                {questionQuery.data.current_phase != null &&
                  ` \u00B7 Phase ${questionQuery.data.current_phase}`}
                {questionQuery.data.repo && ` \u00B7 ${questionQuery.data.repo}`}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-lg leading-none"
            aria-label="Close modal"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="px-4 py-3 space-y-3">
          {/* Loading state */}
          {questionQuery.isLoading && (
            <div className="space-y-2">
              <div className="animate-pulse bg-gray-700 rounded h-4 w-24" />
              <div className="animate-pulse bg-gray-700 rounded h-20" />
            </div>
          )}

          {/* Error state */}
          {questionQuery.isError && (
            <div className="bg-red-900/30 border border-red-700 rounded p-3">
              <p className="text-red-300 text-sm">
                Could not load question. The endpoint may be unavailable.
              </p>
              <button
                onClick={() => questionQuery.refetch()}
                className="mt-2 px-3 py-1 text-xs rounded bg-red-700 hover:bg-red-600 text-red-100"
              >
                Retry
              </button>
            </div>
          )}

          {/* Question loaded */}
          {questionQuery.data && questionQuery.data.has_question_text && (
            <>
              <div>
                <label className="block text-gray-400 text-xs font-medium mb-1">
                  Question:
                </label>
                <div className="bg-gray-900 border border-gray-700 rounded p-3 text-gray-200 text-sm whitespace-pre-wrap max-h-40 overflow-y-auto">
                  {questionQuery.data.question_text}
                </div>
              </div>
            </>
          )}

          {/* Fallback: no question text */}
          {questionQuery.data && !questionQuery.data.has_question_text && (
            <div className="bg-amber-900/20 border border-amber-700/50 rounded p-3">
              <p className="text-amber-300 text-sm">
                Question text not available &mdash; agent posted file path only.
              </p>
              <p className="text-gray-300 text-xs mt-1">
                You can still answer below; this will resume the story.
              </p>
              {questionQuery.data.needs_info_path && (
                <p className="text-gray-400 text-xs mt-1 font-mono break-all">
                  {questionQuery.data.needs_info_path}
                </p>
              )}
            </div>
          )}

          {/* Already answered banner */}
          {alreadyAnswered && (
            <div className="bg-blue-900/30 border border-blue-700 rounded p-3">
              <p className="text-blue-300 text-sm">
                This question has already been answered. The queue will refresh.
              </p>
            </div>
          )}

          {/* Submit error (non-409) */}
          {answerMutation.isError && !alreadyAnswered && (
            <div className="bg-red-900/30 border border-red-700 rounded p-3">
              <p className="text-red-300 text-sm">
                Failed to submit answer. Please try again.
              </p>
            </div>
          )}

          {/* Answer textarea */}
          {questionQuery.data && !alreadyAnswered && (
            <>
              <div>
                <label className="block text-gray-400 text-xs font-medium mb-1">
                  Your Answer:
                </label>
                <textarea
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  disabled={isSubmitting}
                  rows={4}
                  maxLength={65536}
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-gray-200 text-sm resize-y focus:outline-none focus:border-violet-500 disabled:opacity-50"
                  placeholder="Type your answer..."
                />
                <div className="text-gray-500 text-xs mt-1 text-right">
                  {answer.length.toLocaleString()} / 65,536
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        {questionQuery.data && !alreadyAnswered && (
          <div className="px-4 py-3 border-t border-gray-700 flex justify-end">
            <button
              onClick={() => answerMutation.mutate()}
              disabled={!canSubmit}
              className="px-4 py-1.5 text-sm font-medium rounded bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {isSubmitting ? 'Submitting...' : 'Submit Answer'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
