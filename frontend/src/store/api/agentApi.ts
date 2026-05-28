import { baseApi } from './baseApi'
import type { AgentInvokeRequest, AgentInvokeResponse } from '../../types/chat'
import { getAgentId } from '../../lib/chat'

export const agentApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    invokeAgent: builder.mutation<
      AgentInvokeResponse,
      { agentId?: string; body: AgentInvokeRequest }
    >({
      query: ({ agentId = getAgentId(), body }) => ({
        url: `/agents/${agentId}/invoke`,
        method: 'POST',
        body,
      }),
      invalidatesTags: (_result, error) =>
        error ? [] : [{ type: 'Session' as const, id: 'LIST' }],
    }),
  }),
})

export const { useInvokeAgentMutation } = agentApi
