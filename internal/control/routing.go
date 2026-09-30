package control

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"time"
)

var routingAccountIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

const maximumRoutingRequestBytes = 64 * 1024

func (s *Server) routing(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	if request.URL.RawQuery != "" {
		writeJSON(response, http.StatusBadRequest, map[string]any{"error": "routing request query is invalid"})
		return
	}
	switch request.Method {
	case http.MethodGet:
		writeJSON(response, http.StatusOK, map[string]any{"routing": s.mux.RoutingPreference()})
	case http.MethodPatch:
		accountID, err := decodeRoutingPreference(request)
		if err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		ctx, cancel := contextWithRoutingTimeout(request)
		defer cancel()
		preference, err := s.mux.SetRoutingPreference(ctx, accountID)
		if err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"routing": preference})
	default:
		methodNotAllowed(response)
	}
}

func decodeRoutingPreference(request *http.Request) (*string, error) {
	data, err := io.ReadAll(io.LimitReader(request.Body, maximumRoutingRequestBytes+1))
	if err != nil {
		return nil, &routingInputError{"invalid routing update"}
	}
	if len(data) > maximumRoutingRequestBytes {
		return nil, &routingInputError{"routing update is too large"}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	var input map[string]json.RawMessage
	if err := decoder.Decode(&input); err != nil {
		return nil, &routingInputError{"invalid routing update"}
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, &routingInputError{"routing update must contain one JSON object"}
	}
	rawAccountID, ok := input["accountId"]
	if len(input) != 1 || !ok {
		return nil, &routingInputError{"routing update must contain only accountId"}
	}
	var accountID *string
	if err := json.Unmarshal(rawAccountID, &accountID); err != nil {
		return nil, &routingInputError{"accountId must be null or a valid subscription identifier"}
	}
	if accountID != nil && !routingAccountIDPattern.MatchString(*accountID) {
		return nil, &routingInputError{"accountId must be null or a valid subscription identifier"}
	}
	return accountID, nil
}

type routingInputError struct {
	message string
}

func (e *routingInputError) Error() string {
	return e.message
}

func contextWithRoutingTimeout(request *http.Request) (context.Context, context.CancelFunc) {
	return context.WithTimeout(request.Context(), 20*time.Second)
}
