package mux

import (
	"context"
	"errors"
	"fmt"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func (m *Multiplexer) RoutingPreference() state.RoutingPreference {
	return m.store.RoutingPreference()
}

func (m *Multiplexer) SetRoutingPreference(ctx context.Context, accountID *string) (state.RoutingPreference, error) {
	if accountID != nil {
		account, ok := m.store.Account(*accountID)
		if !ok {
			return state.RoutingPreference{}, fmt.Errorf("subscription %q was not found", *accountID)
		}
		if !account.Enabled {
			return state.RoutingPreference{}, fmt.Errorf("subscription %q is disabled", account.Label)
		}
		snapshot, err := m.accountSnapshotWithProfile(ctx, account.ID, false)
		if err != nil || !snapshot.Connected || snapshot.AuthType != "chatgpt" {
			return state.RoutingPreference{}, fmt.Errorf("subscription %q is not connected", account.Label)
		}
	}
	if err := m.store.SetRoutingPreference(accountID); err != nil {
		return state.RoutingPreference{}, err
	}
	preference := m.store.RoutingPreference()
	m.publish(Event{
		Type:    "routing-updated",
		Message: "New chat routing preference changed",
		Data:    preference,
	})
	return preference, nil
}

func (m *Multiplexer) accountForNewThread(ctx context.Context) (state.Account, RouteReason, error) {
	preference := m.store.RoutingPreference()
	if preference.AccountID == nil {
		return m.chooseAccount(ctx)
	}
	account, ok := m.store.Account(*preference.AccountID)
	if !ok || !account.Enabled {
		return state.Account{}, RouteReason{}, errors.New("selected subscription is unavailable; choose another subscription or Automatic")
	}
	snapshot, err := m.accountSnapshotWithProfile(ctx, account.ID, false)
	if err != nil || !snapshot.Connected || snapshot.AuthType != "chatgpt" {
		return state.Account{}, RouteReason{}, fmt.Errorf("selected subscription %q is unavailable; choose another subscription or Automatic", account.Label)
	}
	if manualAccountDepleted(snapshot) {
		return state.Account{}, RouteReason{}, fmt.Errorf("selected subscription %q is depleted; choose another subscription or Automatic", account.Label)
	}
	return account, routeReasonForSnapshot(snapshot), nil
}

func manualAccountDepleted(snapshot AccountSnapshot) bool {
	weekly, short := longestAndShortestWindow(snapshot.RateLimits)
	return weekly != nil && weekly.UsedPercent >= 100 ||
		short != nil && short.UsedPercent >= 100
}

func routeReasonForSnapshot(snapshot AccountSnapshot) RouteReason {
	reason := RouteReason{ThreadCount: snapshot.ThreadCount}
	weekly, short := longestAndShortestWindow(snapshot.RateLimits)
	if weekly != nil {
		weeklyUsed := weekly.UsedPercent
		reason.WeeklyUsedPercent = &weeklyUsed
		if weekly.ResetsAt != nil {
			resetsAt := *weekly.ResetsAt
			reason.WeeklyResetsAt = &resetsAt
		}
	}
	if short != nil {
		shortUsed := short.UsedPercent
		reason.ShortUsedPercent = &shortUsed
	}
	return reason
}
