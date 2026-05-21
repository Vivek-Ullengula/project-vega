import { baseApi } from './baseApi'
import type { SessionDetail, SessionSummary } from '../../types/session'

export const sessionApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    listSessions: builder.query<SessionSummary[], void>({
      query: () => '/sessions',
      providesTags: (result) =>
        result
          ? [
              ...result.map(({ session_id }) => ({ type: 'Session' as const, id: session_id })),
              { type: 'Session' as const, id: 'LIST' },
            ]
          : [{ type: 'Session' as const, id: 'LIST' }],
    }),
    getSession: builder.query<SessionDetail, string>({
      query: (sessionId) => `/sessions/${encodeURIComponent(sessionId)}`,
      providesTags: (_result, _error, id) => [{ type: 'Session' as const, id }],
    }),
    deleteSession: builder.mutation<{ message: string }, string>({
      query: (sessionId) => ({
        url: `/sessions/${encodeURIComponent(sessionId)}`,
        method: 'DELETE',
      }),
      invalidatesTags: (_result, _error, id) => [
        { type: 'Session' as const, id },
        { type: 'Session' as const, id: 'LIST' },
      ],
    }),
  }),
})

export const {
  useListSessionsQuery,
  useGetSessionQuery,
  useLazyGetSessionQuery,
  useDeleteSessionMutation,
} = sessionApi
